"""Three-node multi-view DAG; model execution stays in gated independent workers."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from .backend_registry import ResolvedPlan
from .compiled_plan import digest
from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .dag_image_adapters import _published_files
from .dag_persistence import DagRepository
from .errors import classify_error
from .models import ArtifactRef, BuildRun, NodeAttempt, StructuredValue
from .multi_view_workflow import (
    _backend_metadata,
    _cameras,
    _component,
    _material,
    _native_frame,
    _validate_depths,
    _validate_geometry_spatial_contract,
    _validate_mesh_frame,
    build_multi_view_asset,
    prepared_backend_binding,
)
from .observations import observation_bundle_from_artifact
from .operators import GeometryFrontendOutput, ReconstructionOutput
from .runtime import utc_now
from .serialization import canonical_json_bytes, read_json, to_primitive
from .workbench_models import read_build_run


@dataclass(frozen=True)
class MultiViewProfile:
    plan: ResolvedPlan
    identity: dict[str, Any]
    check: Callable[[], None] | None = None
    test_only: bool = False

    @property
    def fingerprint(self) -> str:
        return digest(
            {
                "identity": self.identity,
                "test_only": self.test_only,
                "contract": self.plan.contract_digest,
                "backends": {
                    k: {"name": b.name, "version": b.backend_version}
                    for k, b in self.plan.backends.items()
                },
            }
        )


def _ref(context: NodeExecutionContext, key: str) -> ArtifactRef:
    value = context.inputs[key]
    if not isinstance(value, ArtifactRef):
        raise ContractError(f"{key} requires an ArtifactRef")
    return value


def geometry_from_evidence(raw: dict[str, Any]) -> GeometryFrontendOutput:
    return GeometryFrontendOutput(
        [StructuredValue(**v) for v in raw["cameras"]],
        [ArtifactRef(**v) for v in raw["depths"]],
        ArtifactRef(**raw["points"]),
        raw["backend_metadata"],
        raw.get("cache_hit", False),
    )


def reconstruction_from_evidence(raw: dict[str, Any]) -> ReconstructionOutput:
    return ReconstructionOutput(
        ArtifactRef(**raw["mesh"]),
        StructuredValue(**raw["material"]),
        StructuredValue(**raw["native_frame"]),
        [StructuredValue(**v) for v in raw["components"]],
        raw["backend_metadata"],
        raw.get("cache_hit", False),
    )


def validate_geometry(context: NodeExecutionContext, value: GeometryFrontendOutput) -> None:
    bundle = observation_bundle_from_artifact(_ref(context, "observations"), context.store)
    cameras = _cameras(context.store, value.cameras, bundle)
    _validate_depths(context.store, value.depths, bundle)
    _validate_geometry_spatial_contract(context.store, cameras, value.depths, value.points)
    _backend_metadata(value.backend_metadata, "geometry")


def validate_reconstruction(
    context: NodeExecutionContext, value: ReconstructionOutput, geometry: GeometryFrontendOutput
) -> None:
    components = [_component(v, context.store) for v in value.components]
    if not components or len({c.component_id for c in components}) != len(components):
        raise ContractError("reconstruction requires unique components")
    if any(c.artifact != value.mesh or c.provenance_ids for c in components):
        raise ContractError("reconstruction component mesh or unverified provenance invalid")
    _material(value.material, context.store)
    native = _native_frame(value.native_frame)
    _validate_mesh_frame(context.store, value.mesh, native)
    if (
        native.unit
        != context.store.get_manifest(geometry.points.artifact_id).identity.identity_metadata[
            "unit"
        ]
    ):
        raise ContractError("reconstruction unit differs from geometry")
    _backend_metadata(value.backend_metadata, "reconstruction")


class _Adapter:
    name: str
    operator: str
    process = True

    def __init__(self, profile: MultiViewProfile):
        self.profile = profile

    @property
    def child_pipeline(self) -> tuple[str, str]:
        return self.name, "1"

    @property
    def spec(self) -> AdapterSpec:
        properties = {"profile_digest": {"type": "string", "enum": [self.profile.fingerprint]}}
        defaults: dict[str, Any] = {"profile_digest": self.profile.fingerprint}
        if not self.process:
            properties["appearance_mode"] = {
                "type": "string",
                "enum": ["preserve_mesh", "apply_material"],
            }
            defaults["appearance_mode"] = "preserve_mesh"
        return AdapterSpec(
            self.name,
            "1",
            (self.operator,),
            {"type": "object", "properties": properties, "required": list(properties)},
            defaults,
            "process" if self.process else "cpu",
            uses_child_run=True,
        )

    def _check(self, context: NodeExecutionContext) -> None:
        if context.parameters["profile_digest"] != self.profile.fingerprint:
            raise ContractError("multi-view profile changed")
        if context.child_context is None:
            raise ContractError("multi-view adapter requires owned child execution")
        if self.profile.check:
            self.profile.check()

    def _backend(self, context: NodeExecutionContext, key: str) -> Any:
        self._check(context)
        if context.worker is None:
            raise ContractError("multi-view model requires gated worker")
        backend = copy.copy(self.profile.plan.backends[key].implementation)
        if hasattr(backend, "worker"):
            backend.worker = context.worker
        elif not self.profile.test_only:
            raise ContractError("backend cannot accept gated worker")
        return backend

    def _origin(self, context: NodeExecutionContext) -> dict[str, Any]:
        return {
            "run_id": context.run_id,
            "node_id": context.node_id,
            "attempt_id": context.attempt_id,
            "profile_digest": self.profile.fingerprint,
            "backend_identity": self.profile.identity,
        }

    def _persist(
        self, context: NodeExecutionContext, schema: str, raw: dict[str, Any]
    ) -> ArtifactRef:
        node_id = "estimate_geometry" if schema == "GeometryFrontendEvidence" else "reconstruct"
        return context.store.persist_structured(
            StructuredValue(
                "quality_evidence",
                schema,
                "1.0",
                {
                    **raw,
                    "origin": self._origin(context),
                    "backend_binding": prepared_backend_binding(self.profile.plan, node_id),
                },
            )
        )

    def _child(self, context: NodeExecutionContext) -> BuildRun:
        assert context.child_context is not None
        inputs = {
            key: value
            for key, value in context.inputs.items()
            if isinstance(value, (ArtifactRef, StructuredValue))
        }
        run = BuildRun(
            context.child_context.registration.child_run_id,
            *self.child_pipeline,
            "running",
            inputs,
            [],
            utc_now(),
            None,
        )
        context.child_context.begin(run)
        return run

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        run = self._child(context)
        assert context.child_context is not None
        attempt = NodeAttempt(
            "compute",
            1,
            self.operator,
            self.name,
            "running",
            "executed",
            run.started_at,
            None,
            None,
        )
        run.node_attempts.append(attempt)
        context.child_context.persist(run)
        try:
            self._check(context)
            outputs = self._compute(context)
            self._check(context)
            attempt.outputs = outputs
            attempt.status = "succeeded"
            attempt.finished_at = utc_now()
            run.status = "succeeded"
            run.finished_at = utc_now()
            context.child_context.persist(run)
            return NodeExecutionResult(outputs)
        except Exception as error:
            attempt.status = "failed"
            attempt.error_code = classify_error(error).value
            attempt.finished_at = utc_now()
            run.status = "failed"
            run.finished_at = utc_now()
            context.child_context.persist(run)
            raise

    def _compute(self, context: NodeExecutionContext) -> dict[str, Any]:
        raise NotImplementedError

    def recover(self, context: NodeExecutionContext) -> NodeExecutionResult | None:
        self._check(context)
        assert context.child_context is not None
        repo = context.child_context.repository
        assert isinstance(repo, DagRepository)
        child_id = context.child_context.registration.child_run_id
        ref = ArtifactRef(**read_json(repo.store.root / "runs" / f"{child_id}.json"))
        repo.verify_reference_closure(ref)
        child = read_build_run(repo.store.read_structured(ref))
        if child.status != "succeeded":
            return None
        if (
            child.run_id != child_id
            or child.parent_run_id != context.run_id
            or (child.pipeline_name, child.pipeline_version) != self.child_pipeline
        ):
            raise ContractError("multi-view child ownership mismatch")
        expected_inputs = {
            k: v for k, v in context.inputs.items() if isinstance(v, (ArtifactRef, StructuredValue))
        }
        if child.inputs != expected_inputs:
            raise ContractError("multi-view child input mismatch")
        if len(child.node_attempts) != 1 or child.node_attempts[0].status != "succeeded":
            raise ContractError("multi-view child result invalid")
        attempt = child.node_attempts[0]
        if (attempt.node_id, attempt.operator, attempt.backend) != (
            "compute",
            self.operator,
            self.name,
        ):
            raise ContractError("multi-view child compute identity mismatch")
        outputs = child.node_attempts[0].outputs
        evidence = outputs["evidence"]
        assert isinstance(evidence, ArtifactRef)
        raw = context.store.read_structured(evidence)
        if raw["origin"] != self._origin(context):
            raise ContractError("multi-view recovered origin mismatch")
        self._validate_recovered(context, raw)
        expected: dict[str, Any] = {"evidence": evidence}
        if isinstance(self, GeometryAdapter):
            value = geometry_from_evidence(raw)
            expected.update(cameras=value.cameras, depths=value.depths, points=value.points)
        if canonical_json_bytes(outputs) != canonical_json_bytes(expected):
            raise ContractError("recovered outputs differ from fixed evidence")
        return NodeExecutionResult(outputs)

    def _validate_recovered(self, context: NodeExecutionContext, raw: dict[str, Any]) -> None:
        raise NotImplementedError


class GeometryAdapter(_Adapter):
    name = "multi_view_geometry"
    operator = "dag_geometry_frontend@1"

    def _compute(self, context: NodeExecutionContext) -> dict[str, Any]:
        value = self._backend(context, "estimate_geometry").estimate(
            context.store, _ref(context, "observations")
        )
        validate_geometry(context, value)
        raw = {**to_primitive(value), "observations": to_primitive(_ref(context, "observations"))}
        evidence = self._persist(context, "GeometryFrontendEvidence", raw)
        return {
            "cameras": value.cameras,
            "depths": value.depths,
            "points": value.points,
            "evidence": evidence,
        }

    def _validate_recovered(self, context: NodeExecutionContext, raw: dict[str, Any]) -> None:
        if raw["observations"] != to_primitive(_ref(context, "observations")):
            raise ContractError("geometry observation mismatch")
        validate_geometry(context, geometry_from_evidence(raw))


class ReconstructionAdapter(_Adapter):
    name = "multi_view_reconstruction"
    operator = "dag_reconstruction@1"

    def _compute(self, context: NodeExecutionContext) -> dict[str, Any]:
        raw = context.store.read_structured(_ref(context, "geometry_evidence"))
        if raw["origin"]["profile_digest"] != self.profile.fingerprint:
            raise ContractError("geometry backend identity differs from reconstruction profile")
        geometry = geometry_from_evidence(raw)
        value = self._backend(context, "reconstruct").reconstruct(
            context.store,
            _ref(context, "observations"),
            geometry.cameras,
            geometry.depths,
            geometry.points,
        )
        validate_reconstruction(context, value, geometry)
        evidence = self._persist(
            context,
            "ReconstructionEvidence",
            {
                **to_primitive(value),
                "observations": to_primitive(_ref(context, "observations")),
                "geometry_evidence": to_primitive(_ref(context, "geometry_evidence")),
            },
        )
        return {"evidence": evidence}

    def _validate_recovered(self, context: NodeExecutionContext, raw: dict[str, Any]) -> None:
        if raw["geometry_evidence"] != to_primitive(_ref(context, "geometry_evidence")) or raw[
            "observations"
        ] != to_primitive(_ref(context, "observations")):
            raise ContractError("reconstruction lineage mismatch")
        geometry = geometry_from_evidence(
            context.store.read_structured(_ref(context, "geometry_evidence"))
        )
        validate_reconstruction(context, reconstruction_from_evidence(raw), geometry)


class ReleaseAdapter(_Adapter):
    name = "multi_view_release"
    operator = "dag_multi_view_release@1"
    process = False

    @property
    def child_pipeline(self) -> tuple[str, str]:
        return self.profile.plan.pipeline_name, self.profile.plan.pipeline_version

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        try:
            return self._execute_release(context)
        except Exception as error:
            assert context.child_context is not None
            child = context.child_context.repository.load(
                context.child_context.registration.child_run_id
            )
            if child.status == "running" and not child.node_attempts:
                child.status = "failed"
                child.finished_at = utc_now()
                child.node_attempts.append(
                    NodeAttempt(
                        "preflight",
                        1,
                        self.operator,
                        self.name,
                        "failed",
                        "executed",
                        child.started_at,
                        child.finished_at,
                        classify_error(error).value,
                    )
                )
                context.child_context.persist(child)
            raise

    def _execute_release(self, context: NodeExecutionContext) -> NodeExecutionResult:
        self._check(context)
        assert context.child_context is not None and context.output_path is not None
        g = context.store.read_structured(_ref(context, "geometry_evidence"))
        r = context.store.read_structured(_ref(context, "reconstruction_evidence"))
        self._check_evidence(g, r)
        result = build_multi_view_asset(
            observations=_ref(context, "observations"),
            store_path=context.store.root,
            output_path=context.output_path,
            resolved_plan=self.profile.plan,
            export_appearance_mode=cast(Any, context.parameters["appearance_mode"]),
            run_id=context.child_context.registration.child_run_id,
            child_context=context.child_context,
            prepared_geometry=geometry_from_evidence(g),
            prepared_reconstruction=reconstruction_from_evidence(r),
            prepared_evidence={
                "geometry": _ref(context, "geometry_evidence"),
                "reconstruction": _ref(context, "reconstruction_evidence"),
            },
        )
        return NodeExecutionResult(
            {
                "asset": result.asset_definition,
                "release": result.release_manifest,
                "glb": result.glb,
                "qa": result.quality_report,
            }
        )

    def _check_evidence(self, g: dict[str, Any], r: dict[str, Any]) -> None:
        if any(raw["origin"]["profile_digest"] != self.profile.fingerprint for raw in (g, r)):
            raise ContractError("prepared backend identity differs from release profile")

    def recover(self, context: NodeExecutionContext) -> NodeExecutionResult | None:
        from .dag_image_adapters import _published_child

        self._check(context)
        child = _published_child(context, self.child_pipeline)
        if child is None:
            return None
        g = _ref(context, "geometry_evidence")
        r = _ref(context, "reconstruction_evidence")
        self._check_evidence(context.store.read_structured(g), context.store.read_structured(r))
        if (
            child.inputs.get("observations") != _ref(context, "observations")
            or child.inputs.get("prepared_geometry") != g
            or child.inputs.get("prepared_reconstruction") != r
        ):
            raise ContractError("release prepared inputs mismatch")
        profile = child.inputs.get("export_profile")
        if (
            not isinstance(profile, StructuredValue)
            or profile.value.get("appearance_mode") != context.parameters["appearance_mode"]
        ):
            raise ContractError("release appearance profile mismatch")
        outputs = next(a.outputs for a in child.node_attempts if a.node_id == "export")
        release = cast(ArtifactRef, outputs["release"])
        raw = context.store.read_structured(release)
        files = {k: ArtifactRef(**v) for k, v in raw["files"].items()}
        asset = ArtifactRef(**raw["asset_definition"])
        _published_files(context, {**files, "asset.json": asset, "release.json": release})
        return NodeExecutionResult(
            {
                "asset": asset,
                "release": release,
                "glb": outputs["glb"],
                "qa": files["qa/quality-report.json"],
            }
        )

"""Profile-pinned single-image adapters for the generic, owned DAG runtime."""

from __future__ import annotations

import json
from copy import copy
from dataclasses import replace
from pathlib import Path
from typing import Any

from .artifact_store import LocalArtifactStore, create_manifest
from .compiled_plan import digest
from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .instance_proposals import prepare_selection_mask, propose_instances, select_instance_proposals
from .models import ArtifactAnnotations, ArtifactRef, BlobIdentity, BuildRun, StructuredValue
from .serialization import canonical_json_bytes, read_json, sha256_bytes, to_primitive
from .workbench_binding import create_selection_binding, verify_imported_binding
from .workbench_engine import BackendProfile
from .workbench_models import MaskDraft
from .workflow import build_image_asset


class _ComparisonStore(LocalArtifactStore):
    """Compute deterministic identities in memory; never publish recovery evidence."""

    def __init__(self, store: LocalArtifactStore):
        self.__dict__.update(store.__dict__)

    def persist_bytes(
        self,
        data: bytes,
        *,
        kind: str,
        schema_name: str,
        schema_version: str,
        identity_metadata: dict[str, Any] | None = None,
        annotations: ArtifactAnnotations | None = None,
    ) -> ArtifactRef:
        manifest = create_manifest(
            BlobIdentity(sha256_bytes(data), len(data)),
            kind=kind,
            schema_name=schema_name,
            schema_version=schema_version,
            identity_metadata=identity_metadata,
            annotations=annotations,
        )
        return ArtifactRef(manifest.artifact_id)


def _ref(context: NodeExecutionContext, name: str) -> ArtifactRef:
    ref = context.inputs.get(name)
    if not isinstance(ref, ArtifactRef) or not context.store.verify_digest(ref):
        raise ContractError(f"missing or corrupt artifact input: {name}")
    return ref


def _owned(context: NodeExecutionContext) -> tuple[Path, str]:
    if context.child_context is None or context.output_path is None:
        raise ContractError("image adapters require an owned child run and output path")
    return context.output_path, context.child_context.registration.child_run_id


def _materialize(context: NodeExecutionContext, reference: ArtifactRef, name: str) -> Path:
    output, _ = _owned(context)
    if not context.store.verify_digest(reference):
        raise ContractError("cannot materialize corrupt input")
    directory = output.parent / "inputs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(context.store.blob_path(reference).read_bytes())
    return path


def _published_child(context: NodeExecutionContext, pipeline: tuple[str, str]) -> BuildRun | None:
    output, run_id = _owned(context)
    assert context.child_context is not None
    repository = context.child_context.repository
    reference = ArtifactRef(**read_json(repository.store.root / "runs" / f"{run_id}.json"))
    from .dag_persistence import DagRepository

    if not isinstance(repository, DagRepository):
        raise ContractError("DAG recovery requires a DAG repository")
    repository.verify_reference_closure(reference)
    child = repository.load(run_id)
    if child.status != "succeeded":
        return None
    if (
        child.parent_run_id != context.run_id
        or (child.pipeline_name, child.pipeline_version) != pipeline
        or not child.node_attempts
        or any(attempt.status != "succeeded" for attempt in child.node_attempts)
    ):
        raise ContractError("child execution identity or attempts differ from adapter")
    if canonical_json_bytes(json.loads((output / "run.json").read_text())) != canonical_json_bytes(
        child
    ):
        raise ContractError("child publication does not match durable run")
    return child


def _published_files(context: NodeExecutionContext, files: dict[str, ArtifactRef]) -> None:
    output, _ = _owned(context)
    for name, ref in files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ContractError("unsafe publication path")
        if (
            not context.store.verify_digest(ref)
            or (output / relative).read_bytes() != context.store.blob_path(ref).read_bytes()
        ):
            raise ContractError("child publication differs from output artifact")


def _profile_digest(profile: BackendProfile) -> str:
    return digest(
        {
            "name": profile.name,
            "proposal": profile.proposal_identity,
            "shape": profile.shape_identity,
            "test_only": profile.test_only,
            "pipeline": profile.shape_plan.pipeline_name,
            "version": profile.shape_plan.pipeline_version,
            "contract": profile.shape_plan.contract_digest,
            "backends": {
                key: {
                    "name": value.name,
                    "operator": value.operator,
                    "version": value.backend_version,
                }
                for key, value in profile.shape_plan.backends.items()
            },
        }
    )


class _ProfileAdapter:
    def __init__(self, profile: BackendProfile):
        self.profile = profile

    def _spec(self, name: str, operator: str, *, generate: bool = False) -> AdapterSpec:
        identity = _profile_digest(self.profile)
        properties: dict[str, Any] = {
            "profile_digest": {"type": "string", "enum": [identity]},
        }
        defaults: dict[str, Any] = {"profile_digest": identity}
        if generate:
            properties.update(
                {
                    "seed": {"type": "integer"},
                    "pipeline_type": {
                        "type": "string",
                        "enum": ["512", "1024", "1024_cascade", "1536_cascade"],
                    },
                }
            )
            defaults.update(seed=42, pipeline_type="512")
        return AdapterSpec(
            name,
            "1",
            (operator,),
            {"type": "object", "properties": properties, "required": list(properties)},
            defaults,
            "process",
            uses_child_run=True,
        )

    def _check(self, context: NodeExecutionContext) -> None:
        if context.parameters.get("profile_digest") != _profile_digest(self.profile):
            raise ContractError("profile identity differs from bound parameters")
        if context.worker is None:
            raise ContractError("process adapter requires a gated worker")
        if self.profile.identity_check is not None:
            self.profile.identity_check()

    def _worker_copy(self, implementation: Any, context: NodeExecutionContext) -> Any:
        implementation = copy(implementation)
        if hasattr(implementation, "worker"):
            implementation.worker = context.worker
        elif not self.profile.test_only:
            raise ContractError("real backend does not support gated worker injection")
        return implementation


class DagProposalAdapter(_ProfileAdapter):
    child_pipeline = ("instance_proposals", "1")

    @property
    def spec(self) -> AdapterSpec:
        return self._spec("image_proposals", "instance_proposals@1")

    def recover(self, context: NodeExecutionContext) -> NodeExecutionResult | None:
        self._check(context)
        child = _published_child(context, self.child_pipeline)
        if child is None:
            return None
        if child.inputs != {"image": _ref(context, "image")}:
            raise ContractError("proposal child inputs differ from current binding")
        outputs = child.node_attempts[0].outputs
        proposals = outputs["proposals"]
        provenance = outputs["provenance"]
        if not isinstance(proposals, ArtifactRef) or not isinstance(provenance, ArtifactRef):
            raise ContractError("proposal outputs must be artifacts")
        raw = context.store.read_structured(proposals)
        if raw["image"] != to_primitive(_ref(context, "image")):
            raise ContractError("proposal source image differs from binding")
        if not self.profile.test_only and any(
            raw["backend_metadata"].get(key) != self.profile.proposal_identity[key]
            for key in ("checkpoint_digest", "runner_digest")
        ):
            raise ContractError("SAM reported identity differs from profile")
        _published_files(context, {"proposals.json": proposals, "provenance.json": provenance})
        return NodeExecutionResult({"proposals": proposals, "provenance": provenance})

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        self._check(context)
        output, run_id = _owned(context)
        result = propose_instances(
            image_path=_materialize(context, _ref(context, "image"), "image.png"),
            store_path=context.store.root,
            output_path=output,
            backend=self._worker_copy(self.profile.proposer, context),
            run_id=run_id,
            child_context=context.child_context,
        )
        self._check(context)
        proposals = ArtifactRef(**result["proposals"])
        if not self.profile.test_only:
            actual = context.store.read_structured(proposals)["backend_metadata"]
            if any(
                actual.get(key) != self.profile.proposal_identity[key]
                for key in ("checkpoint_digest", "runner_digest")
            ):
                raise ContractError("SAM reported identity differs from profile")
        assert context.child_context is not None
        child = context.child_context.repository.load(run_id)
        return NodeExecutionResult(
            {
                "proposals": proposals,
                "provenance": child.node_attempts[0].outputs["provenance"],
            }
        )


class DagMaskSelectionAdapter:
    spec = AdapterSpec(
        "image_mask_selection",
        "1",
        ("workbench_mask_selection@1",),
        execution_kind="human",
        uses_child_run=True,
    )
    child_pipeline = ("instance_selection", "1")

    def recover(self, context: NodeExecutionContext) -> NodeExecutionResult | None:
        if context.decision is None:
            return None
        child = _published_child(context, self.child_pipeline)
        if child is None:
            return None
        proposals = _ref(context, "proposals")
        if child.inputs != {"proposals": proposals}:
            raise ContractError("selection child proposals differ from binding")
        decision = context.store.read_structured(context.decision)
        payload = decision["payload"]
        raw = context.store.read_structured(proposals)
        indexed = {item["proposal_id"]: item for item in raw["proposals"]}
        final, _, _ = prepare_selection_mask(
            _ComparisonStore(context.store),
            ArtifactRef(**raw["image"]),
            ArtifactRef(**indexed[payload["proposal_id"]]["mask"]),
            invert=payload["invert"],
            keep_largest=payload["keep_largest"],
        )
        draft = MaskDraft(payload["proposal_id"], final, payload["invert"], payload["keep_largest"])
        selection = child.node_attempts[0].outputs["selection"]
        if not isinstance(selection, ArtifactRef):
            raise ContractError("selection output must be an artifact")
        if context.store.read_structured(selection)["reviewer"] != decision["reviewer"].strip():
            raise ContractError("selection reviewer differs from durable decision")
        binding = create_selection_binding(
            _ComparisonStore(context.store),
            original_proposals=proposals,
            selection=selection,
            draft=draft,
        )
        _published_files(
            context,
            {
                "selection.json": selection,
                "object_001.png": final,
                "image.input": ArtifactRef(**raw["image"]),
            },
        )
        return NodeExecutionResult({"selection": selection, "binding": binding})

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        proposals = _ref(context, "proposals")
        if context.decision is None:
            return NodeExecutionResult(
                wait_request=context.store.persist_structured(
                    StructuredValue(
                        "dag_human_request",
                        "DagHumanInputRequest",
                        "1.0",
                        {
                            "run_id": context.run_id,
                            "node_id": context.node_id,
                            "input_digest": context.input_digest,
                            "attempt_id": context.attempt_id,
                            "decision_contract": "single-proposal-mask-edit@1",
                            "proposals": to_primitive(proposals),
                        },
                    )
                )
            )
        decision = context.store.read_structured(context.decision)
        payload = decision["payload"]
        if (
            not isinstance(payload, dict)
            or set(payload) != {"proposal_id", "invert", "keep_largest"}
            or not isinstance(payload["proposal_id"], str)
            or type(payload["invert"]) is not bool
            or type(payload["keep_largest"]) is not bool
        ):
            raise ContractError("mask decision requires proposal_id, invert and keep_largest")
        raw = context.store.read_structured(proposals)
        indexed = {item["proposal_id"]: item for item in raw["proposals"]}
        if payload["proposal_id"] not in indexed:
            raise ContractError("mask decision must select an existing proposal")
        final, _, _ = prepare_selection_mask(
            context.store,
            ArtifactRef(**raw["image"]),
            ArtifactRef(**indexed[payload["proposal_id"]]["mask"]),
            invert=payload["invert"],
            keep_largest=payload["keep_largest"],
        )
        draft = MaskDraft(payload["proposal_id"], final, payload["invert"], payload["keep_largest"])
        output, run_id = _owned(context)
        result = select_instance_proposals(
            proposals=proposals,
            proposal_ids=[draft.proposal_id],
            reviewer=decision["reviewer"],
            store_path=context.store.root,
            output_path=output,
            invert=draft.invert,
            keep_largest=draft.keep_largest,
            run_id=run_id,
            child_context=context.child_context,
        )
        selection = ArtifactRef(**result["selection"])
        binding = create_selection_binding(
            context.store, original_proposals=proposals, selection=selection, draft=draft
        )
        return NodeExecutionResult({"selection": selection, "binding": binding})


class DagImageBuildAdapter(_ProfileAdapter):
    @property
    def child_pipeline(self) -> tuple[str, str]:
        return self.profile.shape_plan.pipeline_name, self.profile.shape_plan.pipeline_version

    @property
    def spec(self) -> AdapterSpec:
        return self._spec("image_build", "workbench_image_workflow@1", generate=True)

    def recover(self, context: NodeExecutionContext) -> NodeExecutionResult | None:
        self._check(context)
        child = _published_child(context, self.child_pipeline)
        if child is None:
            return None
        binding = _ref(context, "binding")
        if child.inputs.get("selection_binding") != binding:
            raise ContractError("generation child selection differs from current binding")
        image, mask = child.inputs.get("source_image"), child.inputs.get("source_mask")
        if not isinstance(image, ArtifactRef) or not isinstance(mask, ArtifactRef):
            raise ContractError("generation child input evidence is incomplete")
        mapping = verify_imported_binding(_ComparisonStore(context.store), binding, image, mask)
        if child.inputs.get("import_binding") != mapping:
            raise ContractError("generation import verification differs from original")
        indexed = {attempt.node_id: attempt for attempt in child.node_attempts}
        release = indexed["export"].outputs["release"]
        glb = indexed["export"].outputs["glb"]
        if not isinstance(release, ArtifactRef) or not isinstance(glb, ArtifactRef):
            raise ContractError("generation output evidence is incomplete")
        raw = context.store.read_structured(release)
        files = {name: ArtifactRef(**ref) for name, ref in raw["files"].items()}
        asset = ArtifactRef(**raw["asset_definition"])
        _published_files(context, {**files, "asset.json": asset, "release.json": release})
        quality = files["qa/quality-report.json"]
        records = [
            context.store.read_structured(ref)
            for name, ref in files.items()
            if name.startswith("provenance/")
        ]
        generated = [record for record in records if record.get("operator") == "shape_generation"]
        if len(generated) != 1:
            raise ContractError("generation must carry one shape provenance")
        record = generated[0]
        if (
            record.get("seed") != context.parameters["seed"]
            or record.get("parameters", {}).get("pipeline_type")
            != context.parameters["pipeline_type"]
        ):
            raise ContractError("generation parameters differ from current binding")
        if (
            not self.profile.test_only
            and record.get("model_digest")
            != self.profile.shape_identity["model"]["snapshot_digest"]
        ):
            raise ContractError("shape backend reported identity differs from profile")
        return NodeExecutionResult({"asset": asset, "release": release, "glb": glb, "qa": quality})

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        self._check(context)
        output, run_id = _owned(context)
        binding = _ref(context, "binding")
        raw = context.store.read_structured(binding)
        shape_plan = self.profile.shape_plan
        original = shape_plan.backends["generate_shape"]
        shape_plan = replace(
            shape_plan,
            backends={
                **shape_plan.backends,
                "generate_shape": replace(
                    original, implementation=self._worker_copy(original.implementation, context)
                ),
            },
        )
        built = build_image_asset(
            image_path=_materialize(context, ArtifactRef(**raw["image"]), "image.png"),
            mask_path=_materialize(context, ArtifactRef(**raw["final_mask"]), "mask.png"),
            store_path=context.store.root,
            output_path=output,
            resolved_plan=shape_plan,
            seed=context.parameters["seed"],
            pipeline_type=context.parameters["pipeline_type"],
            run_id=run_id,
            child_context=context.child_context,
            input_binding=binding,
        )
        self._check(context)
        if not self.profile.test_only:
            release = context.store.read_structured(built.release_manifest)
            records = [
                context.store.read_structured(ArtifactRef(**ref))
                for name, ref in release["files"].items()
                if name.startswith("provenance/")
            ]
            generated = [
                record for record in records if record.get("operator") == "shape_generation"
            ]
            if (
                len(generated) != 1
                or generated[0].get("model_digest")
                != self.profile.shape_identity["model"]["snapshot_digest"]
            ):
                raise ContractError("shape backend reported model identity differs from profile")
        return NodeExecutionResult(
            {
                "asset": built.asset_definition,
                "release": built.release_manifest,
                "glb": built.glb,
                "qa": built.quality_report,
            }
        )

"""Explicit reconstruction joins over spatial inputs and fixed frontend evidence.

Registration is opt-in so existing relation identities and persisted DAG plans do
not change when the multi-view entry point is installed. This validates evidence
consistency, not authenticity of arbitrary externally authored evidence.
"""

from __future__ import annotations

from pathlib import Path

from .contracts import ContractError
from .models import ArtifactRef, StructuredValue
from .observations import observation_bundle_from_artifact
from .relations import (
    RelationValidatorRegistry,
    RelationValidatorSpec,
    RuntimeRelationContext,
    StaticRelationContext,
)
from .serialization import canonical_json_bytes, sha256_bytes, to_primitive

_INPUTS = ("observations", "cameras", "depths", "points", "geometry_evidence")


class MultiViewReconstructionValidator:
    @property
    def spec(self) -> RelationValidatorSpec:
        # Include reused semantic checks as well as this boundary contract.
        source = Path(__file__).parent
        digest = sha256_bytes(
            canonical_json_bytes(
                {
                    name: sha256_bytes((source / name).read_bytes())
                    for name in (
                        "multi_view_relations.py",
                        "multi_view_workflow.py",
                        "observations.py",
                    )
                }
            )
        )
        return RelationValidatorSpec("multi_view_reconstruction", "1", digest)

    def validate_static(self, context: StaticRelationContext) -> None:
        if set(context.inputs) != set(_INPUTS):
            raise ContractError("multi-view relation requires all five reconstruction inputs")
        if set(context.bindings) != set(_INPUTS):
            raise ContractError("multi-view relation requires all reconstruction bindings")
        kinds = (
            "observation_bundle",
            "camera_record",
            "depth_map",
            "point_cloud",
            "quality_evidence",
        )
        for name, kind in zip(_INPUTS, kinds, strict=True):
            port = context.ports[name]
            if port.kinds != (kind,):
                raise ContractError(f"multi-view relation rejects {name} port kind")
            collection = name in {"cameras", "depths"}
            allowed = (
                {"one_or_more"} if name == "cameras" else {"zero_or_more", "one_or_more", "many"}
            )
            if (collection and port.cardinality not in allowed) or (
                not collection and port.cardinality != "one"
            ):
                raise ContractError(f"multi-view relation rejects {name} cardinality")

    def validate_runtime(self, context: RuntimeRelationContext) -> None:
        # Lazy import avoids workflow -> pipeline -> relations import cycles.
        from .multi_view_workflow import (
            _cameras,
            _validate_depths,
            _validate_geometry_spatial_contract,
        )

        if set(context.inputs) != set(_INPUTS):
            raise ContractError("multi-view relation requires all five reconstruction inputs")
        observations = context.values.get("observations")
        cameras = context.values.get("cameras")
        depths = context.values.get("depths")
        points = context.values.get("points")
        evidence = context.values.get("geometry_evidence")
        if not isinstance(observations, ArtifactRef) or not isinstance(points, ArtifactRef):
            raise ContractError("multi-view observations and points must be ArtifactRefs")
        if not isinstance(evidence, ArtifactRef):
            raise ContractError("multi-view geometry_evidence must be an ArtifactRef")
        if (
            not isinstance(cameras, tuple)
            or not cameras
            or not all(isinstance(item, StructuredValue) for item in cameras)
        ):
            raise ContractError("multi-view cameras must be a non-empty structured collection")
        if not isinstance(depths, tuple) or not all(
            isinstance(item, ArtifactRef) for item in depths
        ):
            raise ContractError("multi-view depths must be an ArtifactRef collection")
        camera_values = [item for item in cameras if isinstance(item, StructuredValue)]
        depth_refs = [item for item in depths if isinstance(item, ArtifactRef)]
        for reference in [observations, points, evidence, *depth_refs]:
            if not context.store.verify_digest(reference):
                raise ContractError(f"multi-view input has invalid digest: {reference.artifact_id}")
        for reference, kind in [
            (points, "point_cloud"),
            *[(item, "depth_map") for item in depth_refs],
        ]:
            if context.store.get_manifest(reference.artifact_id).identity.kind != kind:
                raise ContractError(f"multi-view expected {kind} artifact")
        identity = context.store.get_manifest(evidence.artifact_id).identity
        if (identity.kind, identity.schema_name, identity.schema_version) != (
            "quality_evidence",
            "GeometryFrontendEvidence",
            "1.0",
        ):
            raise ContractError("expected GeometryFrontendEvidence@1.0")
        recorded = context.store.read_structured(evidence)
        for name, value in (
            ("observations", observations),
            ("cameras", camera_values),
            ("depths", depth_refs),
            ("points", points),
        ):
            if canonical_json_bytes(recorded.get(name)) != canonical_json_bytes(
                to_primitive(value)
            ):
                raise ContractError(f"multi-view {name} differ from fixed geometry evidence")
        if not isinstance(recorded.get("backend_metadata"), dict):
            raise ContractError("geometry evidence requires backend_metadata")
        bundle = observation_bundle_from_artifact(observations, context.store)
        parsed = _cameras(context.store, camera_values, bundle)
        _validate_depths(context.store, depth_refs, bundle)
        _validate_geometry_spatial_contract(context.store, parsed, depth_refs, points)
        unit = context.store.get_manifest(points.artifact_id).identity.identity_metadata.get("unit")
        if unit not in {"meter", "relative_unit"}:
            raise ContractError("multi-view points require meter or relative_unit")


def register_multi_view_relations(registry: RelationValidatorRegistry) -> None:
    registry.register(MultiViewReconstructionValidator())
    registry.register(MultiViewReleaseValidator())


class MultiViewReleaseValidator:
    @property
    def spec(self) -> RelationValidatorSpec:
        return RelationValidatorSpec(
            "multi_view_release", "1", MultiViewReconstructionValidator().spec.implementation_digest
        )

    def validate_static(self, context: StaticRelationContext) -> None:
        names = {"observations", "geometry_evidence", "reconstruction_evidence"}
        if set(context.inputs) != names or set(context.bindings) != names:
            raise ContractError("multi-view release requires all three evidence bindings")
        for name in names:
            port = context.ports[name]
            kind = "observation_bundle" if name == "observations" else "quality_evidence"
            if port.kinds != (kind,) or port.cardinality != "one":
                raise ContractError(f"multi-view release rejects {name} port")

    def validate_runtime(self, context: RuntimeRelationContext) -> None:
        names = {"observations", "geometry_evidence", "reconstruction_evidence"}
        if set(context.inputs) != names:
            raise ContractError("multi-view release requires all three evidence bindings")
        references: dict[str, ArtifactRef] = {}
        for name in names:
            value = context.values.get(name)
            if not isinstance(value, ArtifactRef) or not context.store.verify_digest(value):
                raise ContractError(f"multi-view release requires valid {name} ArtifactRef")
            references[name] = value
        records = {}
        for name, schema in (
            ("geometry_evidence", "GeometryFrontendEvidence"),
            ("reconstruction_evidence", "ReconstructionEvidence"),
        ):
            identity = context.store.get_manifest(references[name].artifact_id).identity
            if (identity.kind, identity.schema_name, identity.schema_version) != (
                "quality_evidence",
                schema,
                "1.0",
            ):
                raise ContractError(f"multi-view release expected {schema}@1.0")
            record = context.store.read_structured(references[name])
            if record.get("observations") != to_primitive(references["observations"]):
                raise ContractError(f"multi-view release {name} observations mismatch")
            records[name] = record
        if records["reconstruction_evidence"].get("geometry_evidence") != to_primitive(
            references["geometry_evidence"]
        ):
            raise ContractError("multi-view release reconstruction geometry evidence mismatch")
        geometry = records["geometry_evidence"]
        try:
            cameras = tuple(StructuredValue(**item) for item in geometry["cameras"])
            depths = tuple(ArtifactRef(**item) for item in geometry["depths"])
            points = ArtifactRef(**geometry["points"])
        except (KeyError, TypeError, ValueError) as error:
            raise ContractError(f"invalid geometry evidence: {error}") from error
        MultiViewReconstructionValidator().validate_runtime(
            RuntimeRelationContext(
                context.operator,
                _INPUTS,
                {
                    "observations": references["observations"],
                    "cameras": cameras,
                    "depths": depths,
                    "points": points,
                    "geometry_evidence": references["geometry_evidence"],
                },
                context.store,
            )
        )

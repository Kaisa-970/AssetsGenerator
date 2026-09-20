"""Assemble an asset from an explicitly related prepared-image shape and QA."""

from collections.abc import Mapping
from pathlib import Path

import numpy as np

from .artifact_store import LocalArtifactStore
from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .models import (
    AABB,
    AppearanceSet,
    ArtifactRef,
    AssetDefinition,
    AssetSpatialInfo,
    GeometrySet,
    PortValue,
    SemanticInfo,
    StructuredValue,
)
from .operators import _load_scene, _scene_vertices, material_from_glb
from .serialization import cache_key, sha256_bytes, to_primitive


def validate_shape_asset_inputs(
    store: LocalArtifactStore,
    inputs: Mapping[str, PortValue | list[PortValue]],
    run_id: str | None = None,
) -> None:
    mesh, image, quality, spatial = (
        inputs.get(key) for key in ("mesh", "image", "quality", "spatial")
    )
    if not all(
        isinstance(value, ArtifactRef) for value in (mesh, image, quality)
    ) or not isinstance(spatial, StructuredValue):
        raise ContractError("shape asset requires mesh, image, quality and spatial inputs")
    assert (
        isinstance(mesh, ArtifactRef)
        and isinstance(image, ArtifactRef)
        and isinstance(quality, ArtifactRef)
    )
    if not all(store.verify_digest(ref) for ref in (mesh, image, quality)):
        raise ContractError("shape asset inputs are missing or corrupt")
    metadata = store.get_manifest(mesh.artifact_id).identity.identity_metadata
    info = spatial.value
    if (
        info.get("canonical_frame_id") != metadata.get("frame_id")
        or info.get("unit") != metadata.get("unit")
        or metadata.get("up_axis") != "+Z"
        or metadata.get("forward_axis") != "+X"
        or info.get("forward_status") != metadata.get("forward_status")
        or info.get("scale_status") != ("metric" if metadata.get("unit") == "meter" else "relative")
    ):
        raise ContractError("shape asset spatial metadata differs from mesh")
    vertices = _scene_vertices(_load_scene(store.blob_path(mesh).read_bytes()))
    bounds = info.get("aabb", {})
    if (
        not np.allclose(
            np.asarray([bounds.get("minimum"), bounds.get("maximum")], dtype=float),
            [vertices.min(axis=0), vertices.max(axis=0)],
            rtol=1e-6,
            atol=1e-7,
        )
        or info.get("obb") is not None
    ):
        raise ContractError("shape asset bounds differ from mesh")
    report = store.read_structured(quality)
    if report.get("profile") != "geometry-v1" or report.get("overall_status") not in {
        "pass",
        "warn",
    }:
        raise ContractError("shape asset requires passing geometry QA")
    records = []
    for ref in store.find_artifacts("provenance_record"):
        if store.verify_digest(ref):
            record = store.read_structured(ref)
            if run_id is None or record.get("run_id") == run_id:
                records.append(record)
    qa = [
        r
        for r in records
        if r.get("operator") == "geometry_validation"
        and r.get("output_artifact_id") == quality.artifact_id
        and mesh.artifact_id in r.get("derived_from_artifact_ids", [])
    ]
    canonical = [
        r
        for r in records
        if r.get("operator") == "canonicalize" and r.get("output_artifact_id") == mesh.artifact_id
    ]
    for q in qa:
        for c in canonical:
            native = (
                c.get("parameters", {})
                .get("resolved_inputs", {})
                .get("mesh", {})
                .get("artifact_id")
            )
            if native not in q.get("derived_from_artifact_ids", []):
                continue
            if any(
                r.get("operator") == "shape_generation"
                and r.get("output_artifact_id") == native
                and image.artifact_id in r.get("derived_from_artifact_ids", [])
                and r.get("run_id") == c.get("run_id") == q.get("run_id")
                for r in records
            ):
                return
    raise ContractError(
        "shape asset inputs do not share generation, canonicalization and QA lineage"
    )


class ShapeAssetAssemblyAdapter:
    @property
    def spec(self) -> AdapterSpec:
        source = sha256_bytes(Path(__file__).with_name("operators.py").read_bytes())
        return AdapterSpec(
            "shape_asset_assembly",
            "1",
            ("shape_asset_assembly@1",),
            parameter_schema={
                "type": "object",
                "properties": {
                    "operator_source_digest": {"type": "string", "enum": [source]},
                    "name": {"type": "string"},
                },
            },
            defaults={"operator_source_digest": source, "name": "generated asset"},
        )

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        validate_shape_asset_inputs(context.store, dict(context.inputs), context.run_id)
        mesh, quality, spatial = (context.inputs[key] for key in ("mesh", "quality", "spatial"))
        assert (
            isinstance(mesh, ArtifactRef)
            and isinstance(quality, ArtifactRef)
            and isinstance(spatial, StructuredValue)
        )
        info = AssetSpatialInfo(**{**spatial.value, "aabb": AABB(**spatial.value["aabb"])})
        asset = AssetDefinition(
            "asset_"
            + cache_key(
                {"run": context.run_id, "node": context.node_id, "attempt": context.attempt_id}
            )[7:],
            "1.0",
            str(context.parameters["name"]),
            GeometrySet([mesh]),
            AppearanceSet([material_from_glb(context.store, mesh)]),
            info,
            SemanticInfo(None, "unknown"),
            None,
            [],
            [quality.artifact_id],
            [],
        )
        reference = context.store.persist_structured(
            StructuredValue("asset_definition", "AssetDefinition", "1.0", to_primitive(asset))
        )
        return NodeExecutionResult({"asset": reference})

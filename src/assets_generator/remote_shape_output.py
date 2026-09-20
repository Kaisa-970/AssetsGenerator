"""Self-contained shape transport; remote metadata never bypasses native-frame checks."""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np

from .artifact_store import LocalArtifactStore
from .models import BackendNativeFrame, PBRMaterial, StructuredValue
from .operators import ShapeOutput, _load_scene, _scene_vertices
from .remote_protocol import decode_remote_json
from .remote_service_worker import ServiceOutput
from .serialization import canonical_json_bytes, sha256_bytes, to_primitive
from .spatial import validate_mesh_native_frame
from .workbench_models import _decode


def import_shape_output(store: LocalArtifactStore, blobs: Mapping[str, bytes]) -> ShapeOutput:
    if set(blobs) != {"mesh", "shape_metadata"}:
        raise ValueError("shape result requires mesh and shape_metadata")
    raw = decode_remote_json(blobs["shape_metadata"])
    if not isinstance(raw, dict) or set(raw) != {
        "mesh_identity",
        "material",
        "native_frame",
        "backend_metadata",
    }:
        raise ValueError("invalid shape metadata envelope")
    identity = raw["mesh_identity"]
    if not isinstance(identity, dict) or set(identity) != {
        "kind",
        "schema_name",
        "schema_version",
        "blob_digest",
        "identity_metadata",
    }:
        raise ValueError("invalid shape mesh identity")
    if (identity["kind"], identity["schema_name"], identity["schema_version"]) != (
        "triangle_mesh",
        "glTF",
        "2.0",
    ):
        raise ValueError("shape requires triangle_mesh glTF 2.0")
    if sha256_bytes(blobs["mesh"]) != identity["blob_digest"]:
        raise ValueError("shape mesh digest mismatch")
    native = _decode(StructuredValue, raw["native_frame"])
    material = _decode(StructuredValue, raw["material"])
    if (native.kind, native.schema_name, native.schema_version) != (
        "backend_native_frame",
        "BackendNativeFrame",
        "1.0",
    ):
        raise ValueError("invalid native frame contract")
    if (material.kind, material.schema_name, material.schema_version) != (
        "pbr_material",
        "PBRMaterial",
        "1.0",
    ):
        raise ValueError("invalid material contract")
    frame = _decode(BackendNativeFrame, native.value)
    validate_mesh_native_frame(identity["identity_metadata"], frame)
    pbr = _decode(PBRMaterial, material.value)
    if len(pbr.base_color_factor) != 4 or any(
        not math.isfinite(v) or not 0 <= v <= 1 for v in pbr.base_color_factor
    ):
        raise ValueError("invalid material color factor")
    if pbr.alpha_mode not in {"OPAQUE", "MASK", "BLEND"}:
        raise ValueError("invalid material alpha mode")
    if any(
        getattr(pbr, field) is not None
        for field in (
            "base_color_texture",
            "normal_texture",
            "metallic_roughness_texture",
            "emissive_texture",
        )
    ):
        raise ValueError("standalone material textures require explicit transport support")
    scene = _load_scene(blobs["mesh"])
    vertices = _scene_vertices(scene)
    if not np.isfinite(vertices).all() or not any(len(g.faces) for g in scene.geometry.values()):
        raise ValueError("shape mesh requires finite nonempty geometry")
    if not isinstance(raw["backend_metadata"], dict):
        raise ValueError("invalid backend metadata")
    mesh = store.persist_bytes(
        blobs["mesh"],
        kind="triangle_mesh",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata=identity["identity_metadata"],
    )
    return ShapeOutput(mesh, material, native, raw["backend_metadata"])


def export_shape_output(store: LocalArtifactStore, output: ShapeOutput) -> dict[str, ServiceOutput]:
    if not store.verify_digest(output.mesh):
        raise ValueError("shape mesh evidence missing/corrupt")
    manifest = store.get_manifest(output.mesh.artifact_id)
    blobs = {
        "mesh": store.blob_path(output.mesh).read_bytes(),
        "shape_metadata": canonical_json_bytes(
            {
                "mesh_identity": to_primitive(manifest.identity),
                "material": to_primitive(output.material),
                "native_frame": to_primitive(output.native_frame),
                "backend_metadata": output.backend_metadata,
            }
        ),
    }
    import_shape_output(store, blobs)
    return {
        "mesh": ServiceOutput(blobs["mesh"], "model/gltf-binary"),
        "shape_metadata": ServiceOutput(blobs["shape_metadata"], "application/json"),
    }

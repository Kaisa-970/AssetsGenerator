"""Verify the SAM3D REST result boundary before publishing a shape envelope."""

from __future__ import annotations

import io
from typing import Any

import numpy as np
from PIL import Image

from .artifact_store import LocalArtifactStore
from .mesh_io import load_scene, scene_vertices
from .models import BackendNativeFrame, PBRMaterial, StructuredValue
from .operators import ShapeOutput
from .remote_protocol import decode_remote_json
from .remote_service_worker import ServiceOutput
from .remote_shape_output import export_shape_output, validate_self_contained_glb
from .serialization import canonical_json_bytes, sha256_bytes, to_primitive

REQUIRED_FILES = ("model.glb", "mask.png", "parameters.json", "evidence.json")


def verify_result(
    store: LocalArtifactStore,
    *,
    files: dict[str, bytes],
    status: dict[str, Any],
    intent: dict[str, Any],
    original_mask: bytes,
) -> dict[str, ServiceOutput]:
    """No missing-blob repair: caller must distinguish first import from recovery."""
    if set(files) != set(REQUIRED_FILES):
        raise ValueError("SAM3D result requires mesh, actual mask, parameters and evidence")
    descriptors = status.get("file_descriptors")
    if not isinstance(descriptors, dict):
        raise ValueError("SAM3D result has no file digests")
    for name, data in files.items():
        if descriptors.get(name) != {"sha256": sha256_bytes(data), "byte_length": len(data)}:
            raise ValueError("SAM3D result file digest/length mismatch")
    evidence = decode_remote_json(files["evidence.json"])
    if not isinstance(evidence, dict) or evidence.get("schema") != "sam3d-result@1":
        raise ValueError("invalid SAM3D boundary evidence")
    request = evidence.get("request")
    if (
        request != intent["request_identity"]
        or evidence.get("request_digest") != intent["request_digest"]
        or evidence.get("deployment") != intent["deployment"]
        or status.get("deployment") != intent["deployment"]
        or status.get("request_digest") != intent["request_digest"]
        or status.get("submission_key") != intent["submission_key"]
    ):
        raise ValueError("SAM3D result belongs to another request or deployment")
    for name in ("model.glb", "mask.png", "parameters.json"):
        if evidence.get("files", {}).get(name) != descriptors[name]:
            raise ValueError("SAM3D evidence differs from terminal descriptors")
    parameters = decode_remote_json(files["parameters.json"])
    if not isinstance(parameters, dict) or parameters != evidence.get("actual_parameters"):
        raise ValueError("SAM3D actual parameter evidence mismatch")
    if parameters.get("mesh_status") != "completed" or any(
        parameters.get(key) != value for key, value in request["options"].items()
    ):
        raise ValueError("SAM3D result parameters differ from requested parameters")
    with (
        Image.open(io.BytesIO(files["mask.png"])) as actual,
        Image.open(io.BytesIO(original_mask)) as expected,
    ):
        actual.load()
        expected.load()
        if (
            actual.format != "PNG"
            or actual.mode not in {"L", "1"}
            or getattr(actual, "n_frames", 1) != 1
            or "transparency" in actual.info
            or actual.size != expected.size
            or actual.convert("L").tobytes() != expected.convert("L").tobytes()
        ):
            raise ValueError("SAM3D actual mask differs from input")
    spatial = intent["deployment"].get("spatial")
    if evidence.get("spatial") != spatial or not isinstance(spatial, dict):
        raise ValueError("SAM3D spatial evidence mismatch")
    if any(
        spatial.get(key) != value
        for key, value in {
            "frame_id": "sam3d_glb",
            "handedness": "right",
            "up_axis": "+Y",
            "forward_axis": None,
            "unit": "relative",
            "metric_scale_verified": False,
            "mesh_row_vector_transform": [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
        }.items()
    ):
        raise ValueError("unsupported SAM3D output coordinate declaration")
    mesh_data = files["model.glb"]
    validate_self_contained_glb(mesh_data)
    scene = load_scene(mesh_data)
    if not np.isfinite(scene_vertices(scene)).all() or not any(
        len(geometry.faces) for geometry in scene.geometry.values()
    ):
        raise ValueError("SAM3D mesh requires finite nonempty geometry")
    # The service has already rotated the mesh. Map the unit name, not the vertices.
    frame = BackendNativeFrame("sam3d_glb", "right", "+Y", None, "unknown", "relative_unit")
    mesh = store.persist_bytes(
        mesh_data,
        kind="triangle_mesh",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={
            "media_type": "model/gltf-binary",
            "frame_id": frame.frame_id,
            "unit": frame.unit,
            "up_axis": frame.up_axis,
            "forward_axis": None,
        },
    )
    output = ShapeOutput(
        mesh,
        StructuredValue(
            "pbr_material", "PBRMaterial", "1.0", to_primitive(PBRMaterial([1.0, 1.0, 1.0, 1.0]))
        ),
        StructuredValue("backend_native_frame", "BackendNativeFrame", "1.0", to_primitive(frame)),
        {
            "backend": "sam3d",
            "backend_digest": intent["backend_digest"],
            "appearance_mode": "preserve_mesh",
            "boundary_evidence": evidence,
            "input_processing": evidence.get("input_processing"),
            "unit_mapping": {"relative": "relative_unit"},
        },
    )
    outputs = export_shape_output(store, output)
    outputs["sam3d_evidence"] = ServiceOutput(canonical_json_bytes(evidence), "application/json")
    outputs["actual_mask"] = ServiceOutput(files["mask.png"], "image/png")
    return outputs

"""Content checks shared by generic remote models, independent of model names."""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

from .contracts import ContractError


def image_metadata(data: bytes, kind: str, schema: str, version: str, media: str) -> dict[str, Any]:
    """Decode the actual raster; never relabel bytes or silently remove alpha."""
    if (schema, version, media) != ("png", "1.0", "image/png"):
        raise ContractError("generic PNG image requires png@1.0 and image/png")
    expected = {"rgb_image": "RGB", "rgba_image": "RGBA", "binary_mask": "L"}[kind]
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 64 * 1024 * 1024:
                raise ContractError("generic image exceeds pixel limit")
            if (
                image.format != "PNG"
                or image.mode != expected
                or getattr(image, "n_frames", 1) != 1
                or "transparency" in image.info
                or image.getexif().get(274, 1) != 1
            ):
                raise ContractError("generic image encoding disagrees with declared kind")
            image.load()
            if kind == "binary_mask" and any(
                v not in {0, 255} for _, v in (image.getcolors(257) or [(0, -1)])
            ):
                raise ContractError("generic binary mask contains nonbinary pixels")
            return {"media_type": media, "channel_layout": expected}
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ContractError("invalid generic image content") from error


def validate_output_contract(port: dict[str, Any]) -> None:
    """Reject unsupported encodings before a service can be installed."""
    kind = port["kinds"][0]
    carrier = port["carriers"][0]
    schema = (port["schema_name"], port["schema_version"])
    media = port.get("media_type")
    if carrier == "structured":
        if (kind, *schema) != ("semantic_info", "SemanticInfo", "1.0"):
            raise ContractError("structured output has no registered schema content validator")
        if media not in (None, "application/json"):
            raise ContractError("structured output requires application/json")
        return
    supported = {
        "text": ("plain_text", "1.0", "text/plain"),
        "rgb_image": ("png", "1.0", "image/png"),
        "rgba_image": ("png", "1.0", "image/png"),
        "binary_mask": ("png", "1.0", "image/png"),
        "triangle_mesh": ("glTF", "2.0", "model/gltf-binary"),
        "collision_mesh": ("glTF", "2.0", "model/gltf-binary"),
    }
    if (*schema, media) != supported.get(kind):
        raise ContractError("output has no registered schema content validator")


def validate_structured_content(
    data: bytes, kind: str, schema: str, version: str
) -> dict[str, Any]:
    from .remote_protocol import decode_remote_json

    if (kind, schema, version) != ("semantic_info", "SemanticInfo", "1.0"):
        raise ContractError("structured output has no registered schema content validator")
    try:
        value = decode_remote_json(data)
    except (ValueError, TypeError) as error:
        raise ContractError("invalid structured JSON") from error
    if (
        not isinstance(value, dict)
        or set(value) != {"semantic_class", "source"}
        or not isinstance(value["source"], str)
        or not value["source"].strip()
        or (
            value["semantic_class"] is not None
            and (
                not isinstance(value["semantic_class"], str) or not value["semantic_class"].strip()
            )
        )
    ):
        raise ContractError("invalid SemanticInfo@1.0 content")
    return value


def validate_mesh_content(data: bytes) -> None:
    """Decode geometry before any mesh Artifact is published."""
    import numpy as np
    import trimesh

    from .mesh_io import load_scene, scene_vertices
    from .remote_shape_output import validate_self_contained_glb

    try:
        validate_self_contained_glb(data)
        scene = load_scene(data)
        vertices = scene_vertices(scene)
        if not len(vertices) or not np.isfinite(vertices).all():
            raise ValueError("mesh requires finite vertices")
        for geometry in scene.geometry.values():
            if (
                not isinstance(geometry, trimesh.Trimesh)
                or not len(geometry.vertices)
                or not len(geometry.faces)
                or not np.isfinite(geometry.vertices).all()
                or geometry.faces.ndim != 2
                or geometry.faces.shape[1] != 3
                or geometry.faces.min() < 0
                or geometry.faces.max() >= len(geometry.vertices)
            ):
                raise ValueError("mesh requires valid triangle indices and finite vertices")
    except Exception as error:
        raise ContractError("generic output is not a valid self-contained mesh GLB") from error

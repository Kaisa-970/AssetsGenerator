"""Read appearance facts from verified GLB bytes; never infer visual quality."""

from __future__ import annotations

import json
import struct
from typing import Any


def describe_mesh_appearance(data: bytes, metadata: dict[str, Any]) -> dict[str, Any]:
    if len(data) < 20:
        raise ValueError("invalid GLB")
    magic, version, length, size, kind = struct.unpack_from("<4sIIII", data)
    if magic != b"glTF" or version != 2 or length != len(data) or kind != 0x4E4F534A:
        raise ValueError("invalid GLB header")
    if size > len(data) - 20:
        raise ValueError("truncated GLB JSON")
    raw = json.loads(data[20 : 20 + size])
    primitives = [p for mesh in raw.get("meshes", []) for p in mesh.get("primitives", [])]
    if not primitives:
        raise ValueError("GLB contains no primitives")
    materials = raw.get("materials", [])
    textured = 0
    colored = 0
    for primitive in primitives:
        colored += "COLOR_0" in primitive.get("attributes", {})
        material = materials[primitive["material"]] if "material" in primitive else {}
        pbr = material.get("pbrMetallicRoughness", {})
        textured += any(
            key in container
            for container, key in (
                (pbr, "baseColorTexture"),
                (pbr, "metallicRoughnessTexture"),
                (material, "normalTexture"),
                (material, "emissiveTexture"),
                (material, "occlusionTexture"),
            )
        )
    return {
        "primitive_count": len(primitives),
        "textured_primitives": textured,
        "vertex_colored_primitives": colored,
        "postprocess_mode": metadata.get("postprocess_mode"),
    }

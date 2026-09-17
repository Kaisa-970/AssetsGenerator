"""Isolated CPU TSDF fusion; importing this module does not import Open3D."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image

Array = NDArray[Any]
SCALE_RULE = "median-valid-positive-depth-v1"


def camera_parameters(intrinsics: Any, transform: Any) -> tuple[Array, Array]:
    """Validate an OpenCV pinhole camera and return Open3D's world-to-camera matrix."""
    matrix = np.asarray(intrinsics, dtype=np.float64)
    if (
        matrix.shape != (3, 3)
        or not np.isfinite(matrix).all()
        or not np.allclose(matrix[2], [0, 0, 1], rtol=0, atol=1e-10)
        or not np.allclose([matrix[0, 1], matrix[1, 0]], 0, rtol=0, atol=1e-10)
        or matrix[0, 0] <= 0
        or matrix[1, 1] <= 0
    ):
        raise ValueError("TSDF requires finite positive-focal-length zero-skew pinhole intrinsics")
    camera_world = np.asarray(transform, dtype=np.float64)
    if camera_world.shape != (4, 4) or not np.isfinite(camera_world).all():
        raise ValueError("T_world_camera must be a finite 4x4 matrix")
    rotation = camera_world[:3, :3]
    if (
        not np.allclose(camera_world[3], [0, 0, 0, 1], rtol=0, atol=1e-8)
        or not np.allclose(rotation.T @ rotation, np.eye(3), rtol=0, atol=1e-5)
        or not np.isclose(np.linalg.det(rotation), 1, rtol=0, atol=1e-5)
    ):
        raise ValueError("T_world_camera must be a right-handed rigid transform")
    return matrix, np.asarray(np.linalg.inv(camera_world))


def prepare_depth(depth: Array, mask: Array | None, invalid_value: Any) -> Array:
    values = np.asarray(depth, dtype=np.float64)
    if values.ndim != 2 or not values.size or not np.isfinite(values).all():
        raise ValueError("TSDF depth must be a nonempty finite single-channel image")
    if isinstance(invalid_value, bool) or not isinstance(invalid_value, (int, float)):
        raise ValueError("depth invalid_value must be finite numeric")
    if not np.isfinite(invalid_value):
        raise ValueError("depth invalid_value must be finite numeric")
    valid = (values > 0) & (values != invalid_value)
    if mask is not None:
        if mask.shape != values.shape or not np.isin(mask, [0, 255]).all():
            raise ValueError("TSDF mask must match depth dimensions and contain only 0/255")
        valid &= mask == 255
    result = np.asarray(np.where(valid, values, 0), dtype=np.float32)
    if not np.isfinite(result).all():
        raise ValueError("TSDF depth exceeds float32 range")
    return np.ascontiguousarray(result)


def fusion_scale(depths: list[Array], request: dict[str, Any]) -> dict[str, Any]:
    ratios = {}
    for name, default in (
        ("voxel_size_ratio", 0.01),
        ("sdf_trunc_ratio", 0.04),
        ("depth_trunc_ratio", 3.0),
    ):
        value = request.get(name, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not np.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{name} must be finite and positive")
        ratios[name] = float(value)
    if ratios["sdf_trunc_ratio"] < ratios["voxel_size_ratio"]:
        raise ValueError("sdf_trunc_ratio must be at least voxel_size_ratio")
    valid = [depth[depth > 0] for depth in depths if np.any(depth > 0)]
    if not valid:
        raise ValueError("TSDF input has no valid positive depth after masking")
    reference = float(np.median(np.concatenate(valid).astype(np.float64)))
    return {
        **ratios,
        "scale_rule": SCALE_RULE,
        "reference_depth": reference,
        "voxel_length": reference * ratios["voxel_size_ratio"],
        "sdf_trunc": reference * ratios["sdf_trunc_ratio"],
        "depth_trunc": reference * ratios["depth_trunc_ratio"],
    }


def run(request: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    views = request["views"]
    if not isinstance(views, list) or not views:
        raise ValueError("TSDF requires at least one RGB-D view")
    if not isinstance(request.get("world_frame"), str) or not request["world_frame"]:
        raise ValueError("TSDF requires an explicit world_frame")
    if request.get("unit") not in {"meter", "relative_unit"}:
        raise ValueError("TSDF supports meter or relative_unit")
    prepared = []
    view_ids = set()
    for view in views:
        view_id = view["view_id"]
        if not isinstance(view_id, str) or not view_id or view_id in view_ids:
            raise ValueError("TSDF view_id must be nonempty and unique")
        view_ids.add(view_id)
        matrix, extrinsic = camera_parameters(view["intrinsics"], view["T_world_camera"])
        with Image.open(view["image"]) as image:
            rgb = np.asarray(image.convert("RGB"), dtype=np.uint8).copy()
        with Image.open(view["depth"]) as image:
            depth = np.asarray(image).copy()
        mask = None
        if view.get("mask") is not None:
            with Image.open(view["mask"]) as image:
                mask = np.asarray(image).copy()
        depth = prepare_depth(depth, mask, view["invalid_value"])
        if depth.shape != rgb.shape[:2]:
            raise ValueError("TSDF RGB and depth dimensions must match")
        prepared.append((view_id, rgb, depth, matrix, extrinsic))
    scale = fusion_scale([item[2] for item in prepared], request)
    if not all(
        np.isfinite(scale[name]) and scale[name] > 0
        for name in ("voxel_length", "sdf_trunc", "depth_trunc")
    ):
        raise ValueError("TSDF effective scale must be finite and positive")
    o3d = importlib.import_module("open3d")
    trimesh = importlib.import_module("trimesh")
    volume = o3d.pipelines.integration.ScalableTSDFVolume(
        voxel_length=scale["voxel_length"],
        sdf_trunc=scale["sdf_trunc"],
        color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,
    )
    counts = []
    for view_id, rgb, depth, matrix, extrinsic in prepared:
        accepted = int(np.count_nonzero((depth > 0) & (depth < scale["depth_trunc"])))
        counts.append({"view_id": view_id, "valid_depth_pixels": accepted})
        if not accepted:
            continue
        height, width = depth.shape
        rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
            o3d.geometry.Image(np.ascontiguousarray(rgb)),
            o3d.geometry.Image(depth),
            depth_scale=1.0,
            depth_trunc=scale["depth_trunc"],
            convert_rgb_to_intensity=False,
        )
        intrinsic = o3d.camera.PinholeCameraIntrinsic(
            width, height, matrix[0, 0], matrix[1, 1], matrix[0, 2], matrix[1, 2]
        )
        volume.integrate(rgbd, intrinsic, extrinsic)
    if not any(item["valid_depth_pixels"] for item in counts):
        raise ValueError("TSDF has no depth pixels below the configured depth_trunc")
    native_mesh = volume.extract_triangle_mesh()
    vertices = np.asarray(native_mesh.vertices)
    faces = np.asarray(native_mesh.triangles)
    colors = np.asarray(native_mesh.vertex_colors)
    if (
        vertices.ndim != 2
        or vertices.shape[1:] != (3,)
        or not len(vertices)
        or faces.ndim != 2
        or faces.shape[1:] != (3,)
        or not len(faces)
        or not np.isfinite(vertices).all()
        or colors.shape != vertices.shape
        or not np.isfinite(colors).all()
        or np.any(colors < 0)
        or np.any(colors > 1)
    ):
        raise ValueError("TSDF produced an empty or invalid colored triangle mesh")
    mesh = trimesh.Trimesh(
        vertices=vertices,
        faces=faces,
        vertex_colors=np.rint(colors * 255).astype(np.uint8),
        process=False,
    )
    output_dir = Path(request["output_dir"]).expanduser().absolute()
    output_dir.mkdir(parents=True, exist_ok=True)
    mesh_path = output_dir / "visual.glb"
    mesh_path.write_bytes(trimesh.Scene(mesh).export(file_type="glb"))
    versions = {}
    for name in ("open3d", "numpy", "Pillow", "trimesh"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed-as-distribution"
    return {
        "mesh": str(mesh_path),
        "backend_metadata": {
            **scale,
            "world_frame": request["world_frame"],
            "unit": request["unit"],
            "view_depth_counts": counts,
            "depth_scale": 1.0,
            "extrinsic_convention": "inverse(T_world_camera)",
            "appearance_mode": "preserve_mesh",
            "color_type": "RGB8",
            "vertex_count": len(vertices),
            "face_count": len(faces),
            "fusion_and_export_seconds": time.perf_counter() - started,
            "software_versions": versions,
            "open3d_version": str(o3d.__version__),
            "python_version": sys.version,
        },
    }


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: open3d_runner.py REQUEST.json RESPONSE.json")
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    response = run(request)
    Path(sys.argv[2]).write_text(json.dumps(response, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

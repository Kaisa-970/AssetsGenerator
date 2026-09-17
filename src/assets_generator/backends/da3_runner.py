"""Isolated DA3 inference; importing this module does not import model code or torch."""

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


def preprocessing_geometry(sizes: list[tuple[int, int]], process_res: int) -> list[dict[str, Any]]:
    """Mirror upstream upper_bound_resize, including mixed-size batch center crop."""
    if not sizes or process_res < 14 or any(min(size) < 1 for size in sizes):
        raise ValueError("DA3 requires positive image sizes and process_res >= 14")
    resized = []
    for width, height in sizes:
        scale = process_res / max(width, height)
        boundary = [max(1, int(round(value * scale))) for value in (width, height)]
        # Upstream uses nearest multiple of 14, resolving ties upwards.
        resized.append(tuple(max(1, ((value + 7) // 14) * 14) for value in boundary))
    target_w = min(size[0] for size in resized)
    target_h = min(size[1] for size in resized)
    if min(target_w, target_h) < 14:
        raise ValueError("DA3 images become smaller than one patch")
    return [
        {
            "original_size": [width, height],
            "resized_size": [new_w, new_h],
            "processed_size": [target_w, target_h],
            "crop_left": (new_w - target_w) // 2,
            "crop_top": (new_h - target_h) // 2,
            "scale_x": new_w / width,
            "scale_y": new_h / height,
        }
        for (width, height), (new_w, new_h) in zip(sizes, resized, strict=True)
    ]


def restore_intrinsics(intrinsics: Array, geometry: dict[str, Any]) -> Array:
    matrix = np.asarray(intrinsics, dtype=np.float64).copy()
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError("DA3 intrinsics must be a finite 3x3 matrix")
    if not np.allclose(matrix[2], [0, 0, 1]) or not np.allclose([matrix[0, 1], matrix[1, 0]], 0):
        raise ValueError("DA3 intrinsics must describe a zero-skew pinhole camera")
    matrix[0, 2] += geometry["crop_left"]
    matrix[1, 2] += geometry["crop_top"]
    matrix[0] /= geometry["scale_x"]
    matrix[1] /= geometry["scale_y"]
    if matrix[0, 0] <= 0 or matrix[1, 1] <= 0:
        raise ValueError("DA3 focal lengths must be positive")
    return matrix


def camera_to_world(extrinsics: Array) -> Array:
    matrix = np.asarray(extrinsics, dtype=np.float64)
    if matrix.shape == (3, 4):
        matrix = np.vstack([matrix, [0, 0, 0, 1]])
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError("DA3 extrinsics must be finite 3x4 or 4x4 matrices")
    rotation = matrix[:3, :3]
    if (
        not np.allclose(matrix[3], [0, 0, 0, 1])
        or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-4)
        or not np.isclose(np.linalg.det(rotation), 1, atol=1e-4)
    ):
        raise ValueError("DA3 extrinsics must be a rigid right-handed transform")
    return np.asarray(np.linalg.inv(matrix))


def restore_depth(
    depth: Array, confidence: Array, geometry: dict[str, Any], threshold: float
) -> Array:
    """Nearest sampling using the same pixel-coordinate convention as upstream K.

    Crop margins and rejected predictions are zero; no extrapolation or interpolation
    across invalid depth is performed.
    """
    width, height = geometry["original_size"]
    proc_w, proc_h = geometry["processed_size"]
    if depth.shape != (proc_h, proc_w) or confidence.shape != depth.shape:
        raise ValueError("DA3 depth/confidence shape differs from preprocessing geometry")
    xx = np.arange(width) * geometry["scale_x"] - geometry["crop_left"]
    yy = np.arange(height) * geometry["scale_y"] - geometry["crop_top"]
    valid_x = (xx >= 0) & (xx < proc_w)
    valid_y = (yy >= 0) & (yy < proc_h)
    x = np.clip(np.rint(xx).astype(int), 0, proc_w - 1)
    y = np.clip(np.rint(yy).astype(int), 0, proc_h - 1)
    values = depth[y[:, None], x[None, :]]
    scores = confidence[y[:, None], x[None, :]]
    valid = (
        valid_y[:, None]
        & valid_x[None, :]
        & np.isfinite(values)
        & (values > 0)
        & np.isfinite(scores)
        & (scores >= threshold)
    )
    return np.asarray(np.where(valid, values, 0), dtype=np.float32)


def backproject(depth: Array, intrinsics: Array, transform: Array, stride: int = 1) -> Array:
    if stride < 1:
        raise ValueError("point sampling stride must be positive")
    y, x = np.nonzero(np.isfinite(depth) & (depth > 0))
    y, x = y[::stride], x[::stride]
    z = depth[y, x]
    camera = np.column_stack(
        [
            (x - intrinsics[0, 2]) * z / intrinsics[0, 0],
            (y - intrinsics[1, 2]) * z / intrinsics[1, 1],
            z,
        ]
    )
    return np.asarray(camera @ transform[:3, :3].T + transform[:3, 3])


def prediction_arrays(prediction: Any, count: int) -> tuple[Array, Array, Array, Array]:
    depth = np.asarray(prediction.depth)
    confidence = np.asarray(prediction.conf)
    intrinsics = np.asarray(prediction.intrinsics)
    extrinsics = np.asarray(prediction.extrinsics)
    if depth.ndim != 3 or depth.shape[0] != count or confidence.shape != depth.shape:
        raise ValueError("DA3 must return matching NxHxW depth and confidence arrays")
    if intrinsics.shape != (count, 3, 3) or extrinsics.shape not in ((count, 3, 4), (count, 4, 4)):
        raise ValueError("DA3 camera count or matrix shapes do not match input views")
    return depth, confidence, intrinsics, extrinsics


def write_points(path: Path, points: Array) -> None:
    if points.ndim != 2 or points.shape[1] != 3 or not len(points) or not np.isfinite(points).all():
        raise ValueError("DA3 produced no finite point cloud")
    with path.open("wb") as stream:
        stream.write(
            (
                "ply\nformat binary_little_endian 1.0\n"
                f"element vertex {len(points)}\n"
                "property float x\nproperty float y\nproperty float z\nend_header\n"
            ).encode()
        )
        stream.write(np.asarray(points, dtype="<f4").tobytes())


def run(request: dict[str, Any]) -> dict[str, Any]:
    repo = Path(request["repo"]).expanduser().absolute()
    model_path = Path(request["model"]).expanduser().absolute()
    if not model_path.is_dir():
        raise ValueError("DA3 requires an existing local model snapshot directory")
    sys.path.insert(0, str(repo / "src"))
    # Dynamic imports keep torch and the model package outside the Core environment.
    torch = importlib.import_module("torch")
    api = importlib.import_module("depth_anything_3.api")
    if not torch.cuda.is_available():
        raise RuntimeError("DA3 requires an accessible CUDA GPU")
    observations = request["observations"]
    if len(observations) < 2:
        raise ValueError("DA3 geometry frontend requires at least two views")
    paths = [item["image"] for item in observations]
    sizes = []
    for path in paths:
        with Image.open(path) as image:
            sizes.append(image.size)
    process_res = int(request.get("process_res", 392))
    geometry = preprocessing_geometry(sizes, process_res)
    percentile = float(request.get("confidence_percentile", 40))
    if not np.isfinite(percentile) or not 0 <= percentile <= 100:
        raise ValueError("confidence_percentile must be between 0 and 100")
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    model = api.DepthAnything3.from_pretrained(str(model_path)).to("cuda").eval()
    with torch.inference_mode():
        prediction = model.inference(
            paths,
            process_res=process_res,
            process_res_method="upper_bound_resize",
            export_dir=None,
        )
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - started
    depth, confidence, intrinsics, extrinsics = prediction_arrays(prediction, len(paths))
    finite_scores = confidence[np.isfinite(confidence) & np.isfinite(depth) & (depth > 0)]
    if not finite_scores.size:
        raise ValueError("DA3 returned no valid depth/confidence pixels")
    threshold = float(np.percentile(finite_scores, percentile))
    output_dir = Path(request["output_dir"]).absolute()
    output_dir.mkdir(parents=True, exist_ok=True)
    cameras, depths, point_sets = [], [], []
    # Deterministic cap without random state and without allocating all world points.
    stride = max(1, int(np.ceil(sum(w * h for w, h in sizes) / 200_000)))
    for index, (observation, geo) in enumerate(zip(observations, geometry, strict=True)):
        matrix = restore_intrinsics(intrinsics[index], geo)
        transform = camera_to_world(extrinsics[index])
        restored = restore_depth(depth[index], confidence[index], geo, threshold)
        if not np.any(restored > 0):
            raise ValueError(f"DA3 view {index} has no accepted depth pixels")
        path = output_dir / f"depth-{index:04d}.tiff"
        Image.fromarray(restored).save(path)
        depths.append({"view_id": observation["view_id"], "path": str(path)})
        frame = f"da3_camera_{index}"
        cameras.append(
            {
                "camera_id": f"da3_camera_{index}",
                "image_view_id": observation["view_id"],
                "model": "pinhole",
                "width": sizes[index][0],
                "height": sizes[index][1],
                "fx": float(matrix[0, 0]),
                "fy": float(matrix[1, 1]),
                "cx": float(matrix[0, 2]),
                "cy": float(matrix[1, 2]),
                "distortion": [],
                "camera_frame_id": frame,
                "source": "estimated",
                "T_world_camera": {
                    "source_frame_id": frame,
                    "target_frame_id": "da3_world",
                    "matrix": transform.tolist(),
                },
            }
        )
        point_sets.append(backproject(restored, matrix, transform, stride))
    points = np.concatenate(point_sets)
    points_path = output_dir / "points.ply"
    write_points(points_path, points)
    versions = {}
    for package in ("torch", "torchvision", "numpy", "Pillow", "depth-anything-3"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed-as-distribution"
    return {
        "cameras": cameras,
        "depths": depths,
        "points": str(points_path),
        "backend_metadata": {
            "process_res": process_res,
            "process_res_method": "upper_bound_resize",
            "preprocessing": geometry,
            "depth_resampling": "nearest-original-pixel-coordinates",
            "invalid_value": 0,
            "confidence_percentile": percentile,
            "confidence_threshold": threshold,
            "point_sampling_stride": stride,
            "point_count": len(points),
            "unit": "relative_unit",
            "world_frame": "da3_world",
            "inference_and_load_seconds": elapsed,
            "peak_cuda_memory_mb": torch.cuda.max_memory_allocated() / 1024**2,
            "peak_cuda_reserved_mb": torch.cuda.max_memory_reserved() / 1024**2,
            "software_versions": versions,
            "python_version": sys.version,
            "cuda_version": torch.version.cuda,
        },
    }


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: da3_runner.py REQUEST.json RESPONSE.json")
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    response = run(request)
    Path(sys.argv[2]).write_text(json.dumps(response, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

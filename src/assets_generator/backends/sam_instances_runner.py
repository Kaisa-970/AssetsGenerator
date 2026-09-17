"""Isolated SAM v1 proposals from a local checkpoint; no downloads or semantic labels."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

POLICY = "sam-v1-area-desc-mask-digest-v1"


def environment_identity(module: Any) -> dict[str, Any]:
    """Fingerprint installed SAM sources and critical distribution manifests."""
    packages = {}
    for name in ("segment_anything", "torch", "torchvision", "numpy", "Pillow"):
        try:
            distribution = importlib.metadata.distribution(name)
            metadata = {
                field: distribution.read_text(field)
                for field in ("RECORD", "METADATA", "WHEEL", "direct_url.json")
            }
            packages[name] = {
                "version": distribution.version,
                "installation_digest": "sha256:"
                + hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest(),
            }
        except importlib.metadata.PackageNotFoundError:
            packages[name] = {"version": "not-installed-as-distribution"}
    sources = {}
    location = getattr(module, "__file__", None)
    if location:
        root = Path(location).parent
        sources = {
            str(path.relative_to(root)): _digest(path) for path in sorted(root.rglob("*.py"))
        }
    identity = {
        "python_executable": str(Path(sys.executable).absolute()),
        "python_prefix": sys.prefix,
        "python_version": sys.version,
        "packages": packages,
        "sam_source_digests": sources,
    }
    return {
        **identity,
        "environment_digest": "sha256:"
        + hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def parameters(request: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, default, upper in (
        ("points_per_side", 16, 64),
        ("max_instances", 20, 256),
        ("min_area_pixels", 64, None),
    ):
        value = request.get(name, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 1
            or (upper is not None and value > upper)
        ):
            raise ValueError(
                f"{name} must be a positive integer" + (f" <= {upper}" if upper else "")
            )
        values[name] = value
    for name, threshold_default in (("pred_iou_thresh", 0.88), ("stability_score_thresh", 0.95)):
        value = request.get(name, threshold_default)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise ValueError(f"{name} must be finite and between 0 and 1")
        values[name] = float(value)
    return values


def run(request: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    if not isinstance(request, dict):
        raise ValueError("SAM request must be a JSON object")
    options = parameters(request)
    model_type = request.get("model_type", "vit_h")
    if model_type not in {"vit_h", "vit_l", "vit_b"}:
        raise ValueError("model_type must be vit_h, vit_l or vit_b")
    device = request.get("device", "cuda")
    if not isinstance(device, str) or not re.fullmatch(r"cpu|cuda(?::[0-9]+)?", device):
        raise ValueError("device must be cpu, cuda or cuda:<index>")
    paths = {}
    for name in ("image", "output_dir", "checkpoint"):
        value = request.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a local path")
        paths[name] = Path(value).expanduser().absolute()
    checkpoint = paths["checkpoint"]
    if not checkpoint.is_file():
        raise ValueError("SAM requires an existing local checkpoint; downloads are disabled")
    with Image.open(paths["image"]) as source:
        image = np.asarray(source.convert("RGB"))
    checkpoint_digest = _digest(checkpoint)
    # Import only inside the isolated worker. Official SAM registry loads the supplied
    # local checkpoint and has no model-hub/download fallback.
    sam = importlib.import_module("segment_anything")
    model = sam.sam_model_registry[model_type](checkpoint=str(checkpoint))
    model.to(device=device)
    model.eval()
    generator = sam.SamAutomaticMaskGenerator(
        model=model,
        points_per_side=options["points_per_side"],
        points_per_batch=min(64, options["points_per_side"] ** 2),
        pred_iou_thresh=options["pred_iou_thresh"],
        stability_score_thresh=options["stability_score_thresh"],
        crop_n_layers=0,
        output_mode="binary_mask",
    )
    raw = generator.generate(image)
    if _digest(checkpoint) != checkpoint_digest:
        raise ValueError("SAM checkpoint changed during inference")
    proposals = []
    for item in raw:
        mask = np.asarray(item["segmentation"])
        if mask.shape != image.shape[:2] or mask.dtype != np.bool_:
            raise ValueError("SAM output must contain image-sized boolean masks")
        area = int(np.count_nonzero(mask))
        if area < options["min_area_pixels"]:
            continue
        scores = {}
        for name in ("predicted_iou", "stability_score"):
            value = item[name]
            if isinstance(value, (bool, np.bool_)) or not isinstance(
                value, (int, float, np.number)
            ):
                raise ValueError(f"SAM returned invalid {name}")
            score = float(value)
            if not math.isfinite(score):
                raise ValueError(f"SAM returned nonfinite {name}")
            scores[name] = score
        if (
            scores["predicted_iou"] < options["pred_iou_thresh"]
            or scores["stability_score"] < options["stability_score_thresh"]
        ):
            continue
        y, x = np.nonzero(mask)
        bbox = [int(x.min()), int(y.min()), int(x.max() - x.min() + 1), int(y.max() - y.min() + 1)]
        digest = hashlib.sha256(mask.tobytes()).hexdigest()
        proposals.append((area, digest, scores, bbox, mask))
    proposals.sort(
        key=lambda value: (
            -value[0],
            value[1],
            -value[2]["predicted_iou"],
            -value[2]["stability_score"],
        )
    )
    output = paths["output_dir"]
    output.mkdir(parents=True, exist_ok=True)
    descriptors: list[dict[str, Any]] = []
    seen = set()
    for area, digest, scores, bbox, mask in proposals:
        if digest in seen:
            continue
        seen.add(digest)
        if len(descriptors) >= options["max_instances"]:
            break
        path = output / f"mask-{len(descriptors):04d}.png"
        if path.exists():
            raise FileExistsError(path)
        Image.fromarray(mask.astype(np.uint8) * 255).save(path)
        descriptors.append(
            {"mask": str(path), "area": area, "bbox": bbox, **scores, "label": "unknown"}
        )
    versions = {}
    for name in ("segment_anything", "torch", "torchvision", "numpy", "Pillow"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed-as-distribution"
    return {
        "proposals": descriptors,
        "backend_metadata": {
            "checkpoint_digest": checkpoint_digest,
            "model_type": model_type,
            "device": device,
            "policy": POLICY,
            "parameters": {**options, "crop_n_layers": 0, "output_mode": "binary_mask"},
            "software_versions": versions,
            "python_version": sys.version,
            "proposal_count_before_filter": len(raw),
            "inference_and_export_seconds": time.perf_counter() - started,
            "score_semantics": "uncalibrated_sam_predicted_iou_and_stability",
            "backend_environment": environment_identity(sam),
        },
    }


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: sam_instances_runner.py REQUEST.json RESPONSE.json")
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    result = run(request)
    Path(sys.argv[2]).write_text(
        json.dumps(result, sort_keys=True, allow_nan=False), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

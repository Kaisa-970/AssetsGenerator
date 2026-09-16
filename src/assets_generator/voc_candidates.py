"""Select VOC input candidates independently of generated model quality."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .serialization import sha256_bytes

CLASSES = (
    "background aeroplane bicycle bird boat bottle bus car cat chair cow diningtable "
    "dog horse motorbike person pottedplant sheep sofa train tvmonitor"
).split()
RIGID = {
    "aeroplane",
    "bicycle",
    "boat",
    "bottle",
    "bus",
    "car",
    "chair",
    "diningtable",
    "motorbike",
    "sofa",
    "train",
    "tvmonitor",
}


def prepare_candidates(
    root: Path,
    output: Path,
    *,
    limit: int = 12,
    min_side: int = 96,
    min_fraction: float = 0.05,
    exclude_manifest: Path | None = None,
) -> dict[str, Any]:
    if limit < 1 or min_side < 1 or not 0 <= min_fraction <= 1:
        raise ValueError("invalid candidate selection parameters")
    if output.exists():
        raise FileExistsError(output)
    root = root.resolve()
    excluded = set()
    if exclude_manifest:
        excluded = {
            Path(c["image"]).stem for c in json.loads(exclude_manifest.read_text())["cases"]
        }
    audit: list[dict[str, Any]] = []
    eligible: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for annotation in sorted((root / "SegmentationObject").glob("*.png")):
        image = root / "JPEGImages" / f"{annotation.stem}.jpg"
        with Image.open(annotation) as source:
            labels = np.asarray(source)
        with Image.open(root / "SegmentationClass" / annotation.name) as source:
            classes = np.asarray(source)
        with Image.open(image) as source:
            if source.size != (labels.shape[1], labels.shape[0]) or classes.shape != labels.shape:
                raise ValueError(f"annotation size mismatch: {annotation}")
        per_image = []
        for instance in sorted(int(v) for v in np.unique(labels) if 0 < v < 255):
            mask = labels == instance
            y, x = np.where(mask)
            category_labels = classes[mask]
            category_labels = category_labels[(category_labels > 0) & (category_labels < 21)]
            category = (
                CLASSES[int(np.bincount(category_labels).argmax())]
                if len(category_labels)
                else "unknown"
            )
            width, height = int(x.max() - x.min() + 1), int(y.max() - y.min() + 1)
            pixels = int(mask.sum())
            fraction = pixels / mask.size
            reasons = []
            if annotation.stem in excluded:
                reasons.append("previously_sampled_image")
            if category not in RIGID:
                reasons.append("outside_rigid_object_scope")
            if min(width, height) < min_side:
                reasons.append("small_target_bbox")
            if fraction < min_fraction:
                reasons.append("low_foreground_fraction")
            record = {
                "id": f"voc2012_{annotation.stem}_instance_{instance:02d}",
                "image": str(image),
                "image_id": annotation.stem,
                "instance_id": instance,
                "category": category,
                "image_size": [labels.shape[1], labels.shape[0]],
                "bbox_size": [width, height],
                "foreground_pixels": pixels,
                "foreground_fraction": fraction,
                "touches_image_boundary": bool(
                    x.min() == 0
                    or y.min() == 0
                    or x.max() == mask.shape[1] - 1
                    or y.max() == mask.shape[0] - 1
                ),
                "annotation": str(annotation),
                "reasons": reasons,
            }
            audit.append(record)
            if not reasons:
                per_image.append(record)
        if per_image:
            best = sorted(per_image, key=lambda r: (-r["foreground_pixels"], r["instance_id"]))[0]
            eligible[best["category"]].append(best)
            for record in per_image:
                if record is not best:
                    record["reasons"].append("secondary_eligible_instance")
    # Round-robin category selection avoids the original filename-prefix category bias.
    selected: list[dict[str, Any]] = []
    while len(selected) < limit and any(eligible.values()):
        for category in sorted(eligible):
            if eligible[category] and len(selected) < limit:
                selected.append(eligible[category].pop(0))
    if not selected:
        raise ValueError("no eligible VOC candidates")
    for group in eligible.values():
        for record in group:
            record["reasons"].append("beyond_candidate_limit")
    mask_dir = output.parent / f"{output.stem}-masks"
    mask_dir.mkdir(parents=True, exist_ok=True)
    cases = []
    for record in selected:
        with Image.open(record["annotation"]) as source:
            mask = np.where(np.asarray(source) == record["instance_id"], 255, 0).astype(np.uint8)
        path = mask_dir / f"{record['id']}.png"
        Image.fromarray(mask).save(path)
        cases.append(
            {
                **record,
                "mask": str(path.resolve()),
                "image_digest": sha256_bytes(Path(record["image"]).read_bytes()),
                "mask_digest": sha256_bytes(path.read_bytes()),
                "selection_reason": "largest eligible instance in image; category round-robin",
                "review_status": "pending_input_review",
            }
        )
    dataset = {
        "version": "voc2012-rigid-candidates-v1",
        "source": "PASCAL VOC 2012 segmentation annotations",
        "license": "Original image rights apply; redistribution not asserted",
        "policy": {
            "limit": limit,
            "min_bbox_side": min_side,
            "min_foreground_fraction": min_fraction,
            "rigid_categories": sorted(RIGID),
            "exclude_manifest": str(exclude_manifest) if exclude_manifest else None,
            "threshold_status": "input screening heuristics, not quality acceptance criteria",
        },
        "cases": cases,
        "audit_counts": dict(Counter(reason for r in audit for reason in r["reasons"])),
    }
    output.write_text(json.dumps(dataset, indent=2))
    output.with_suffix(".audit.json").write_text(json.dumps(audit, indent=2))
    return dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--min-side", type=int, default=96)
    parser.add_argument("--min-fraction", type=float, default=0.05)
    parser.add_argument("--exclude-manifest", type=Path)
    args = parser.parse_args()
    result = prepare_candidates(
        args.root,
        args.output,
        limit=args.limit,
        min_side=args.min_side,
        min_fraction=args.min_fraction,
        exclude_manifest=args.exclude_manifest,
    )
    print(json.dumps({"candidates": len(result["cases"]), "audit": result["audit_counts"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

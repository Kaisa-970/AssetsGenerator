"""Prepare small PASCAL VOC instance cases for the benchmark manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def build_manifest(root: Path, output: Path, limit: int) -> None:
    if limit <= 0:
        raise ValueError("limit must be positive")
    root = root.resolve()
    image_dir = root / "JPEGImages"
    annotation_dir = root / "SegmentationObject"
    mask_dir = output.resolve().parent / f"{output.stem}-masks"
    cases: list[dict[str, str]] = []
    for annotation in sorted(annotation_dir.glob("*.png")):
        image = image_dir / f"{annotation.stem}.jpg"
        if not image.is_file():
            continue
        with Image.open(annotation) as source:
            labels = np.asarray(source)
        with Image.open(image) as source:
            if source.size != (labels.shape[1], labels.shape[0]):
                raise ValueError(f"image/mask size mismatch: {annotation}")
        for instance_id in sorted(int(value) for value in np.unique(labels) if 0 < value < 255):
            if len(cases) >= limit:
                break
            mask = mask_dir / f"{annotation.stem}_instance_{instance_id:02d}.png"
            mask.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(
                np.where(labels == instance_id, 255, 0).astype(np.uint8), mode="L"
            ).save(mask)
            cases.append(
                {
                    "id": f"voc2012_{annotation.stem}_instance_{instance_id:02d}",
                    "image": str(image),
                    "mask": str(mask),
                    "source": "PASCAL VOC 2012 SegmentationObject",
                    "license": "Original image rights apply; redistribution not asserted",
                }
            )
        if len(cases) >= limit:
            break
    if not cases:
        raise ValueError("no VOC segmentation instances found")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "version": "voc2012-instances-v2",
                "license": "Original image rights apply; redistribution not asserted",
                "cases": cases,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="VOCdevkit/VOC2012 directory")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    build_manifest(args.root, args.output, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

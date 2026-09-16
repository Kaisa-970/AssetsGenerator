"""Prepare small PASCAL VOC instance cases for the benchmark manifest."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw


def mask_from_xml(annotation: Path, image: Path, output: Path) -> None:
    with Image.open(image) as source:
        mask = Image.new("L", source.size, 0)
    draw = ImageDraw.Draw(mask)
    root = ET.parse(annotation).getroot()
    for obj in root.findall("object"):
        box = obj.find("bndbox")
        if box is None:
            continue
        coords = [int(float(box.findtext(name, "0"))) for name in ("xmin", "ymin", "xmax", "ymax")]
        draw.rectangle(coords, fill=255)
    output.parent.mkdir(parents=True, exist_ok=True)
    mask.save(output, format="PNG")


def build_manifest(root: Path, output: Path, limit: int) -> None:
    image_dir = root / "JPEGImages"
    annotation_dir = root / "SegmentationObject"
    mask_dir = root / "masks"
    cases = []
    for annotation in sorted(annotation_dir.glob("*.png"))[:limit]:
        image = image_dir / f"{annotation.stem}.jpg"
        if not image.is_file():
            continue
        mask = mask_dir / f"{annotation.stem}.png"
        mask.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(annotation).convert("L") as source:
            source.point(lambda value: 255 if value > 0 and value < 255 else 0).save(mask)
        cases.append(
            {
                "id": f"voc2012_{annotation.stem}",
                "image": str(image),
                "mask": str(mask),
                "source": "PASCAL VOC 2012",
                "license": "PASCAL VOC license; verify terms before redistribution",
            }
        )
    output.write_text(
        json.dumps(
            {
                "version": "voc2012-smoke-v1",
                "license": "PASCAL VOC license; verify terms before redistribution",
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

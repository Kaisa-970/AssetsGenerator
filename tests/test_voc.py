import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from assets_generator.voc import build_manifest


def test_build_manifest_preserves_palette_indices_and_separates_instances(tmp_path: Path) -> None:
    root = tmp_path / "VOC2012"
    (root / "JPEGImages").mkdir(parents=True)
    (root / "SegmentationObject").mkdir()
    Image.new("RGB", (4, 2)).save(root / "JPEGImages/0001.jpg")
    labels = np.array([[0, 1, 2, 255], [1, 2, 0, 255]], dtype=np.uint8)
    source = Image.fromarray(labels).convert("P")
    source.putpalette([255, 255, 255] * 256)
    source.save(root / "SegmentationObject/0001.png")
    manifest = tmp_path / "manifest.json"
    build_manifest(root, manifest, 5)
    cases = json.loads(manifest.read_text())["cases"]
    assert len(cases) == 2
    for index, case in enumerate(cases, 1):
        with Image.open(case["mask"]) as mask:
            assert np.array_equal(np.asarray(mask), np.where(labels == index, 255, 0))
    build_manifest(root, manifest, 1)
    assert len(json.loads(manifest.read_text())["cases"]) == 1


def test_empty_dataset_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="no VOC"):
        build_manifest(tmp_path, tmp_path / "manifest.json", 20)

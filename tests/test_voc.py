from pathlib import Path

from PIL import Image

from assets_generator.voc import build_manifest


def test_build_manifest_converts_voc_object_masks(tmp_path: Path) -> None:
    root = tmp_path / "VOC2012"
    (root / "JPEGImages").mkdir(parents=True)
    (root / "SegmentationObject").mkdir()
    Image.new("RGB", (4, 3), (1, 2, 3)).save(root / "JPEGImages" / "0001.jpg")
    Image.new("L", (4, 3), 1).save(root / "SegmentationObject" / "0001.png")
    manifest = tmp_path / "manifest.json"
    build_manifest(root, manifest, 5)
    assert manifest.is_file()
    assert Image.open(root / "masks" / "0001.png").getbbox() is not None

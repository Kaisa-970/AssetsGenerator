import json

import numpy as np
from PIL import Image

from assets_generator.voc_candidates import prepare_candidates


def test_candidates_use_one_rigid_instance_per_image_and_keep_audit(tmp_path):
    root = tmp_path / "VOC"
    for name in ("JPEGImages", "SegmentationObject", "SegmentationClass"):
        (root / name).mkdir(parents=True)
    for name in ("a", "b"):
        labels = np.zeros((10, 10), dtype=np.uint8)
        classes = labels.copy()
        labels[:5] = 1
        classes[:5] = 15  # large person is excluded, not chosen as primary rigid object
        labels[5:8] = 2
        classes[5:8] = 7
        labels[8:] = 3
        classes[8:] = 5
        Image.fromarray(labels).save(root / "SegmentationObject" / f"{name}.png")
        Image.fromarray(classes).save(root / "SegmentationClass" / f"{name}.png")
        Image.new("RGB", (10, 10)).save(root / "JPEGImages" / f"{name}.jpg")
    excluded = tmp_path / "old.json"
    excluded.write_text(json.dumps({"cases": [{"image": "b.jpg"}]}))
    output = tmp_path / "candidates.json"
    dataset = prepare_candidates(
        root, output, limit=10, min_side=2, min_fraction=0.1, exclude_manifest=excluded
    )
    assert json.loads(output.read_text()) == dataset
    assert len(dataset["cases"]) == 1
    case = dataset["cases"][0]
    assert case["category"] == "car"
    assert case["instance_id"] == 2
    assert case["bbox_size"] == [10, 3]
    with Image.open(case["mask"]) as mask:
        assert np.count_nonzero(np.asarray(mask)) == 30
    audit = json.loads(output.with_suffix(".audit.json").read_text())
    assert any("outside_rigid_object_scope" in r["reasons"] for r in audit)
    assert any("previously_sampled_image" in r["reasons"] for r in audit)
    assert any("secondary_eligible_instance" in r["reasons"] for r in audit)

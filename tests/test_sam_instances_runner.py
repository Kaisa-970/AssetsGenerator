from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from assets_generator.backends import sam_instances_runner as runner


def setup(tmp_path, monkeypatch, proposals):
    Image.new("RGBA", (6, 4), (200, 100, 0, 100)).save(tmp_path / "image.png")
    (tmp_path / "checkpoint.pth").write_bytes(b"local-test-checkpoint")
    request = dict(
        image=str(tmp_path / "image.png"),
        checkpoint=str(tmp_path / "checkpoint.pth"),
        output_dir=str(tmp_path / "output"),
        device="cpu",
        min_area_pixels=1,
    )
    seen = {}

    class Model:
        def to(self, **kwargs):
            seen["device"] = kwargs

        def eval(self):
            seen["eval"] = True

    def model(**kwargs):
        seen["model"] = kwargs
        return Model()

    class Generator:
        def __init__(self, **kwargs):
            seen["parameters"] = kwargs

        def generate(self, image):
            assert image.shape == (4, 6, 3) and image.dtype == np.uint8
            return proposals

    module = SimpleNamespace(
        sam_model_registry={"vit_h": model}, SamAutomaticMaskGenerator=Generator
    )
    monkeypatch.setattr(runner.importlib, "import_module", lambda name: module)
    return request, seen


def proposal(mask, score=0.99):
    return dict(
        segmentation=mask, predicted_iou=score, stability_score=0.98, area=-1, bbox=[0, 0, 99, 99]
    )


def test_masks_sorted_deduped_capped_and_bbox_recomputed(tmp_path, monkeypatch):
    small = np.zeros((4, 6), dtype=bool)
    small[1, 3] = True
    big = np.ones((4, 6), dtype=bool)
    request, seen = setup(
        tmp_path, monkeypatch, [proposal(small), proposal(big), proposal(big, 0.97)]
    )
    result = runner.run(request)
    assert seen["eval"]
    assert seen["parameters"]["crop_n_layers"] == 0
    rows = result["proposals"]
    assert [row["area"] for row in rows] == [24, 1]
    assert rows[0]["bbox"] == [0, 0, 6, 4]
    assert rows[1]["bbox"] == [3, 1, 1, 1]
    assert rows[0]["predicted_iou"] == 0.99
    assert all(row["label"] == "unknown" and "confidence" not in row for row in rows)
    assert result["backend_metadata"]["checkpoint_digest"] == runner._digest(
        tmp_path / "checkpoint.pth"
    )
    with Image.open(rows[1]["mask"]) as image:
        np.testing.assert_array_equal(np.asarray(image), small.astype(np.uint8) * 255)
    request["output_dir"] = str(tmp_path / "second")
    request["max_instances"] = 1
    assert len(runner.run(request)["proposals"]) == 1


def test_small_empty_and_low_score_masks_filtered(tmp_path, monkeypatch):
    mask = np.ones((4, 6), dtype=bool)
    request, _ = setup(tmp_path, monkeypatch, [proposal(mask, 0.1), proposal(mask & False)])
    assert runner.run(request)["proposals"] == []


@pytest.mark.parametrize(
    "name,value",
    [
        ("points_per_side", 0),
        ("points_per_side", 65),
        ("points_per_side", True),
        ("max_instances", 0),
        ("max_instances", 257),
        ("min_area_pixels", -1),
        ("pred_iou_thresh", float("nan")),
        ("stability_score_thresh", 1.1),
        ("device", "download"),
        ("model_type", "missing"),
        ("checkpoint", "https://models/file"),
    ],
)
def test_invalid_requests_fail_before_model_import(tmp_path, monkeypatch, name, value):
    request, _ = setup(tmp_path, monkeypatch, [])
    request[name] = value
    monkeypatch.setattr(
        runner.importlib, "import_module", lambda _: pytest.fail("unexpected import")
    )
    with pytest.raises(ValueError):
        runner.run(request)


def test_checkpoint_changes_during_generation_rejected(tmp_path, monkeypatch):
    request, _ = setup(tmp_path, monkeypatch, [])
    original = runner._digest
    calls = 0

    def changed(path):
        nonlocal calls
        calls += 1
        return original(path) if calls == 1 else "sha256:changed"

    monkeypatch.setattr(runner, "_digest", changed)
    with pytest.raises(ValueError, match="changed during"):
        runner.run(request)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("mask", [np.ones((2, 2), dtype=bool), np.ones((4, 6), dtype=np.uint8)])
def test_malformed_worker_masks_rejected(tmp_path, monkeypatch, mask):
    request, _ = setup(tmp_path, monkeypatch, [proposal(mask)])
    with pytest.raises(ValueError, match="boolean masks"):
        runner.run(request)


def test_environment_identity_changes_with_installed_sam_source(tmp_path):
    source = tmp_path / "__init__.py"
    source.write_text("version = 1\n")
    module = SimpleNamespace(__file__=str(source))
    first = runner.environment_identity(module)
    assert first == runner.environment_identity(module)
    source.write_text("version = 2\n")
    second = runner.environment_identity(module)
    assert first["environment_digest"] != second["environment_digest"]
    assert first["sam_source_digests"] != second["sam_source_digests"]

from __future__ import annotations

import json

import pytest
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.observation_import import import_observation_manifest


def _manifest(tmp_path, *, depth_unit="millimeter"):
    Image.new("RGB", (8, 6), "red").save(tmp_path / "front.png")
    Image.new("RGB", (8, 6), "blue").save(tmp_path / "side.png")
    Image.new("L", (8, 6), 255).save(tmp_path / "front-mask.png")
    Image.new("I;16", (8, 6), 1000).save(tmp_path / "side-depth.png")
    manifest = tmp_path / "observations.json"
    manifest.write_text(
        json.dumps(
            {
                "views": [
                    {"view_id": "front", "image": "front.png", "mask": "front-mask.png"},
                    {
                        "view_id": "side",
                        "image": "side.png",
                        "depth": {
                            "path": "side-depth.png",
                            "frame_id": "camera_side",
                            "unit": depth_unit,
                            "invalid_value": 0,
                        },
                        "camera": {
                            "camera_id": "camera_side",
                            "image_view_id": "side",
                            "model": "pinhole",
                            "width": 8,
                            "height": 6,
                            "fx": 10.0,
                            "fy": 10.0,
                            "cx": 4.0,
                            "cy": 3.0,
                            "distortion": [],
                            "camera_frame_id": "camera_side",
                            "T_world_camera": None,
                            "source": "provided",
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_import_observation_manifest_persists_multi_view_bundle(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")

    reference = import_observation_manifest(_manifest(tmp_path), store)
    bundle = store.read_structured(reference)

    assert bundle["observation_id"].startswith("observation_")
    assert [view["view_id"] for view in bundle["views"]] == ["front", "side"]
    assert bundle["views"][1]["depth"] is not None
    assert bundle["views"][1]["camera"]["image_view_id"] == "side"
    assert store.verify_digest(reference)


def test_import_observation_manifest_rejects_missing_depth_unit(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")

    with pytest.raises(ContractError, match="depth requires frame_id and unit"):
        import_observation_manifest(_manifest(tmp_path, depth_unit=None), store)


@pytest.mark.parametrize(
    ("image_mode", "mask_mode", "mask_value", "message"),
    [
        ("L", "L", 255, "rgb_image must use RGB mode"),
        ("RGB", "RGB", (255, 255, 255), "binary_mask must be single-channel"),
        ("RGB", "L", 128, "binary_mask must contain only 0 and 255"),
        ("RGB", "L", 0, "binary_mask must contain foreground pixels"),
    ],
)
def test_import_observation_manifest_rejects_invalid_raster_semantics(
    tmp_path, image_mode, mask_mode, mask_value, message
) -> None:
    Image.new(image_mode, (4, 4), 128 if image_mode == "L" else "red").save(tmp_path / "view.png")
    Image.new(mask_mode, (4, 4), mask_value).save(tmp_path / "mask.png")
    manifest = tmp_path / "observations.json"
    manifest.write_text(
        json.dumps({"views": [{"view_id": "view_000", "image": "view.png", "mask": "mask.png"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ContractError, match=message):
        import_observation_manifest(manifest, LocalArtifactStore(tmp_path / "store"))


@pytest.mark.parametrize(
    "views",
    [None, "bad", [None], [{}], [{"view_id": "view_000"}]],
)
def test_import_observation_manifest_rejects_malformed_views(tmp_path, views) -> None:
    manifest = tmp_path / "observations.json"
    manifest.write_text(json.dumps({"views": views}), encoding="utf-8")

    with pytest.raises(ContractError):
        import_observation_manifest(manifest, LocalArtifactStore(tmp_path / "store"))


def test_import_observation_manifest_rejects_invalid_camera_scalar(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["views"][1]["camera"]["fx"] = "ten"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ContractError, match="non-finite calibration"):
        import_observation_manifest(manifest, LocalArtifactStore(tmp_path / "store"))


def test_import_observation_manifest_requires_provided_camera_source(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["views"][1]["camera"]["source"] = "estimated"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ContractError, match="source must be provided"):
        import_observation_manifest(manifest, LocalArtifactStore(tmp_path / "store"))


def test_import_observation_manifest_is_atomic_on_validation_failure(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    Image.new("L", (2, 2), 255).save(tmp_path / "front-mask.png")
    store = LocalArtifactStore(tmp_path / "store")

    with pytest.raises(ContractError, match="mask dimensions differ"):
        import_observation_manifest(manifest, store)

    assert not list(store.manifests_dir.rglob("*.json"))
    assert not list(store.blobs_dir.rglob("*"))


def test_import_observation_identity_does_not_depend_on_filename_suffix(tmp_path) -> None:
    image = tmp_path / "view.png"
    Image.new("RGB", (4, 4), "red").save(image)
    renamed = tmp_path / "view.dat"
    renamed.write_bytes(image.read_bytes())
    first_manifest = tmp_path / "first.json"
    second_manifest = tmp_path / "second.json"
    first_manifest.write_text(
        json.dumps({"views": [{"view_id": "view_000", "image": image.name}]}),
        encoding="utf-8",
    )
    second_manifest.write_text(
        json.dumps({"views": [{"view_id": "view_000", "image": renamed.name}]}),
        encoding="utf-8",
    )
    store = LocalArtifactStore(tmp_path / "store")

    first = import_observation_manifest(first_manifest, store)
    second = import_observation_manifest(second_manifest, store)

    assert first == second
    first_view = store.read_structured(first)["views"][0]
    manifest = store.get_manifest(first_view["image"]["artifact_id"])
    assert manifest.identity.schema_name == "png"
    assert manifest.identity.identity_metadata["media_type"] == "image/png"

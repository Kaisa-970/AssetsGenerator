"""Content-based observation joins reject unrelated and damaged evidence."""

import io

import pytest
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.dag_asset_assembly import shape_observation_id
from assets_generator.models import ObservationView
from assets_generator.observations import make_observation_bundle, observation_bundle_value
from assets_generator.operators import prepare_observation


def raster(store, mode, color, kind):
    data = io.BytesIO()
    Image.new(mode, (8, 8), color).save(data, format="PNG")
    return store.persist_bytes(
        data.getvalue(),
        kind=kind,
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": mode},
    )


def fixture(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    image = raster(store, "RGB", "red", "rgb_image")
    mask = raster(store, "L", 255, "binary_mask")
    prepared = prepare_observation(store, image, mask)
    observation = store.persist_structured(prepared.bundle)
    return store, image, mask, prepared, observation


@pytest.mark.parametrize("change", ["image", "mask", "no_mask", "multiple_views"])
def test_observation_join_rejects_valid_but_incompatible_bundles(tmp_path, change):
    store, image, mask, prepared, _ = fixture(tmp_path)
    if change == "image":
        image = raster(store, "RGB", "blue", "rgb_image")
    elif change == "mask":
        data = io.BytesIO()
        edited = Image.new("L", (8, 8), 255)
        edited.putpixel((0, 0), 0)
        edited.save(data, format="PNG")
        mask = store.persist_bytes(
            data.getvalue(),
            kind="binary_mask",
            schema_name="png",
            schema_version="1.0",
            identity_metadata={"media_type": "image/png", "channel_layout": "L"},
        )
    views = [ObservationView("view_000", image, mask=None if change == "no_mask" else mask)]
    if change == "multiple_views":
        views.append(ObservationView("view_001", image, mask=mask))
    bundle = make_observation_bundle(views, store)
    ref = store.persist_structured(observation_bundle_value(bundle))
    before = set((store.root / "manifests").rglob("*.json"))
    with pytest.raises(ContractError, match="do not match prepared|require one RGB view"):
        shape_observation_id(store, {"image": prepared.rgba, "observations": ref})
    assert set((store.root / "manifests").rglob("*.json")) == before


@pytest.mark.parametrize("missing", ["image", "mask", "bundle"])
def test_observation_join_never_repairs_missing_source_evidence(tmp_path, missing):
    store, image, mask, prepared, observation = fixture(tmp_path)
    path = store.blob_path({"image": image, "mask": mask, "bundle": observation}[missing])
    path.unlink()
    with pytest.raises(ValueError):
        shape_observation_id(store, {"image": prepared.rgba, "observations": observation})
    assert not path.exists()


def test_matching_observation_id_is_preserved_without_new_artifacts(tmp_path):
    store, _, _, prepared, observation = fixture(tmp_path)
    before = set((store.root / "manifests").rglob("*.json"))
    assert (
        shape_observation_id(store, {"image": prepared.rgba, "observations": observation})
        == prepared.bundle.value["observation_id"]
    )
    assert set((store.root / "manifests").rglob("*.json")) == before

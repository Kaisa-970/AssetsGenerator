import json

import pytest
from test_scene_workflow import setup

from assets_generator.contracts import ContractError
from assets_generator.models import ArtifactRef, StructuredValue
from assets_generator.scene_workflow import build_scene


def test_scene_copies_complete_releases_without_changing_bytes(tmp_path):
    store, path, _, release = setup(tmp_path)
    output = tmp_path / "scene"
    build_scene(manifest_path=path, store_path=store.root, output_path=output)
    for instance in ("first", "second"):
        root = output / "assets" / instance
        assert json.loads((root / "release.json").read_text()) == release
        assert (root / "asset.json").read_bytes() == store.blob_path(
            ArtifactRef(**release["asset_definition"])
        ).read_bytes()
        for name, ref in release["files"].items():
            assert (root / name).read_bytes() == store.blob_path(ArtifactRef(**ref)).read_bytes()
        assert (root / "geometry/visual.glb").is_file()
        assert (root / "qa/quality-report.json").is_file()
        assert not (root / "visual.glb").exists()


@pytest.mark.parametrize(
    "name",
    [
        "../escape.glb",
        "/absolute.glb",
        "nested/../../escape.glb",
        "nested\\escape.glb",
        "C:/escape.glb",
        "asset.json",
        "release.json",
        "asset.json/nested",
        "release.json/nested",
        "./alias.glb",
        "nested//alias.glb",
        ".",
        "",
        "bad\x00name",
        "geometry",
    ],
)
def test_scene_rejects_unsafe_release_files(tmp_path, name):
    store, path, raw, release = setup(tmp_path)
    release["files"][name] = release["files"]["geometry/visual.glb"]
    bad = store.persist_structured(StructuredValue("asset_release", "AssetRelease", "1.0", release))
    raw["instances"][0]["release"] = {"artifact_id": bad.artifact_id}
    path.write_text(json.dumps(raw))
    with pytest.raises(ContractError, match="release path"):
        build_scene(manifest_path=path, store_path=store.root, output_path=tmp_path / "scene")
    assert not (tmp_path / "scene").exists()
    assert not (tmp_path / "escape.glb").exists()


def test_scene_rejects_corrupt_nonmesh_release_file(tmp_path):
    store, path, _, release = setup(tmp_path)
    quality = ArtifactRef(**release["files"]["qa/quality-report.json"])
    store.blob_path(quality).write_bytes(b"corrupted")
    with pytest.raises(ContractError, match="invalid scene asset release file"):
        build_scene(manifest_path=path, store_path=store.root, output_path=tmp_path / "scene")
    assert not (tmp_path / "scene").exists()

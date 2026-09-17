import json
from pathlib import Path

import numpy as np
import pytest
from test_composition import selected_session

from assets_generator.contracts import ContractError
from assets_generator.models import ArtifactRef
from assets_generator.operators import _load_scene, _scene_vertices
from assets_generator.scene_workflow import build_scene


def setup(tmp_path):
    store, session, selected, _ = selected_session(tmp_path)
    result = session.compose(
        {
            "selection_id": selected["selection"]["artifact_id"],
            "regions": {"generated": {"mode": "all"}, "reconstructed": {"mode": "all"}},
        }
    )
    release = store.read_structured(ArtifactRef(**result["release"]))
    asset = store.read_structured(ArtifactRef(**release["asset_definition"]))
    source = (
        f"{release['asset_definition']['artifact_id']}/{asset['spatial']['canonical_frame_id']}"
    )
    matrix = np.array([[0, -2, 0, 2], [2, 0, 0, 3], [0, 0, 2, 4], [0, 0, 0, 1]], dtype=float)
    raw = {
        "schema_version": "1.0",
        "frame_id": "room",
        "unit": "relative_unit",
        "instances": [
            {
                "instance_id": "first",
                "release": result["release"],
                "world_pose": {
                    "source_frame_id": source,
                    "target_frame_id": "room",
                    "matrix": np.eye(4).tolist(),
                },
            },
            {
                "instance_id": "second",
                "release": result["release"],
                "world_pose": {
                    "source_frame_id": source,
                    "target_frame_id": "room",
                    "matrix": matrix.tolist(),
                },
            },
        ],
    }
    path = tmp_path / "scene-input.json"
    path.write_text(json.dumps(raw))
    return store, path, raw, release


def test_scene_reuses_definition_and_maps_canonical_pose(tmp_path):
    store, path, raw, release = setup(tmp_path)
    asset = ArtifactRef(**release["asset_definition"])
    before = store.blob_path(asset).read_bytes()
    result = build_scene(manifest_path=path, store_path=store.root, output_path=tmp_path / "scene")
    definition = store.read_structured(ArtifactRef(**result["scene"]))
    assert len(definition["instances"]) == 2
    for ref in definition["instances"]:
        instance = store.read_structured(ArtifactRef(**ref))
        assert instance["asset_definition"] == release["asset_definition"]
        assert instance["pose_source"] == "user"
    original = _scene_vertices(
        _load_scene(
            store.blob_path(ArtifactRef(**release["files"]["geometry/visual.glb"])).read_bytes()
        )
    )
    scene = _load_scene((tmp_path / "scene/geometry/scene.glb").read_bytes())
    actual = _scene_vertices(scene)
    import trimesh

    conversion = np.array([[0, 1, 0, 0], [0, 0, 1, 0], [1, 0, 0, 0], [0, 0, 0, 1]])
    placement = conversion @ np.array(raw["instances"][1]["world_pose"]["matrix"]) @ conversion.T
    expected = np.vstack([original, trimesh.transform_points(original, placement)])
    assert np.allclose(np.unique(actual, axis=0), np.unique(expected, axis=0))
    assert store.blob_path(asset).read_bytes() == before
    assert {g.visual.kind for g in scene.geometry.values()} == {"vertex", "texture"}
    records = [json.loads(p.read_text()) for p in (tmp_path / "scene/provenance").glob("*.json")]
    assert len({r["provenance_id"] for r in records}) == len(records)
    assert store.get_build_run(result["run_id"])["status"] == "succeeded"


@pytest.mark.parametrize("error", ["duplicate", "unit", "frame", "reflection"])
def test_scene_rejects_ambiguous_placement(tmp_path, error):
    store, path, raw, _ = setup(tmp_path)
    if error == "duplicate":
        raw["instances"][1]["instance_id"] = "first"
    if error == "unit":
        raw["unit"] = "meter"
    if error == "frame":
        raw["instances"][1]["world_pose"]["source_frame_id"] = "asset_canonical"
    if error == "reflection":
        raw["instances"][1]["world_pose"]["matrix"][0][0] = -1
    path.write_text(json.dumps(raw))
    with pytest.raises(ContractError):
        build_scene(manifest_path=path, store_path=store.root, output_path=tmp_path / "scene")
    assert not (tmp_path / "scene").exists()


def test_scene_publish_failure(tmp_path, monkeypatch):
    store, path, _, _ = setup(tmp_path)

    def fail(*args):
        raise OSError("failed publication")

    monkeypatch.setattr(Path, "rename", fail)
    with pytest.raises(OSError):
        build_scene(manifest_path=path, store_path=store.root, output_path=tmp_path / "scene")
    runs = [store.get_build_run(p.stem) for p in (store.root / "runs").glob("run_*.json")]
    run = next(r for r in runs if r["pipeline_name"] == "scene_asset_v1")
    assert run["status"] == "failed"
    assert run["node_attempts"][-1]["error_code"] == "release_failed"
    assert not (tmp_path / "scene").exists()


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {"schema_version": "1.0", "unit": "relative_unit", "frame_id": "room", "instances": [None]},
        {
            "schema_version": "1.0",
            "unit": "relative_unit",
            "frame_id": "room",
            "instances": [{"instance_id": "a"}],
        },
    ],
)
def test_malformed_scene_manifest_is_contract_error(tmp_path, raw):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ContractError):
        build_scene(manifest_path=path, store_path=tmp_path / "store", output_path=tmp_path / "out")


def test_scene_cli_build_and_inspect(tmp_path, monkeypatch, capsys):
    from assets_generator.cli import main

    store, path, _, _ = setup(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "assets-generator",
            "build-scene",
            "--manifest",
            str(path),
            "--store",
            str(store.root),
            "--output",
            str(tmp_path / "cli-scene"),
        ],
    )
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(
        "sys.argv",
        [
            "assets-generator",
            "inspect",
            "--store",
            str(store.root),
            "--artifact",
            result["scene"]["artifact_id"],
        ],
    )
    assert main() == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["verified"] is True
    assert len(inspected["value"]["instances"]) == 2

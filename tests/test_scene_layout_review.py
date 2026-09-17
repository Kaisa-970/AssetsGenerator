import json
import threading
from http.client import HTTPConnection
from pathlib import Path

import numpy as np
import pytest
from test_scene_workflow import setup

from assets_generator.contracts import ContractError
from assets_generator.models import ArtifactRef
from assets_generator.scene_layout_review import (
    SceneLayoutReviewSession,
    _matrix,
    create_scene_layout_review_server,
)


def draft_fixture(tmp_path):
    store, _, raw, _ = setup(tmp_path)
    draft = {
        "schema_version": "1.0",
        "frame_id": raw["frame_id"],
        "unit": raw["unit"],
        "instances": [
            {"instance_id": item["instance_id"], "release": item["release"]}
            for item in raw["instances"]
        ],
    }
    path = tmp_path / "layout-draft.json"
    path.write_text(json.dumps(draft))
    return store, path, draft


def test_publish_builds_scene_and_records_reviewer_evidence(tmp_path):
    store, draft, raw = draft_fixture(tmp_path)
    output = tmp_path / "reviewed-scene"
    session = SceneLayoutReviewSession(store.root, draft, output)
    config = session.config()
    assert config["frame_id"] == "room"
    assert [item["model_url"] for item in config["instances"]] == [
        "/models/0.glb",
        "/models/1.glb",
    ]
    result = session.publish(
        {
            "reviewer": " layout reviewer ",
            "poses": {
                "first": {
                    "translation": {"x": 1, "y": 2, "z": 3},
                    "rotation_degrees": {"yaw": 90, "pitch": 0, "roll": 0},
                    "scale": 2,
                },
                "second": {
                    "translation": {"x": 0, "y": 0, "z": 0},
                    "rotation_degrees": {"yaw": 0, "pitch": 0, "roll": 0},
                    "scale": 1,
                },
            },
        }
    )
    layout = json.loads(Path(result["layout_manifest"]).read_text())
    assert layout["instances"][0]["world_pose"]["target_frame_id"] == raw["frame_id"]
    assert np.allclose(
        layout["instances"][0]["world_pose"]["matrix"],
        [[0, -2, 0, 1], [2, 0, 0, 2], [0, 0, 2, 3], [0, 0, 0, 1]],
    )
    review = store.read_structured(ArtifactRef(**result["layout_review"]))
    assert review["reviewer"] == "layout reviewer"
    assert review["reviewer_identity"] == "self_reported"
    assert review["scene"] == result["scene"]
    assert review["scene_run_id"] == result["run_id"]
    assert review["layout"] == layout
    assert store.verify_digest(ArtifactRef(**review["layout_artifact"]))
    layout_run = store.get_build_run(result["layout_run_id"])
    assert layout_run["status"] == "succeeded"
    assert layout_run["node_attempts"][0]["outputs"]["review"] == result["layout_review"]
    assert output.joinpath("layout-review.json").is_file()
    assert output.joinpath("layout-provenance.json").is_file()
    assert output.joinpath("geometry/scene.glb").is_file()
    assert store.get_build_run(result["run_id"])["status"] == "succeeded"


def test_review_persistence_failure_does_not_publish_scene(tmp_path, monkeypatch):
    store, draft, _ = draft_fixture(tmp_path)
    session = SceneLayoutReviewSession(store.root, draft, tmp_path / "scene")
    original = session.store.persist_structured

    def fail(value):
        if value.kind == "scene_layout_review":
            raise OSError("review persistence failed")
        return original(value)

    monkeypatch.setattr(session.store, "persist_structured", fail)
    poses = {
        item["instance_id"]: {
            "translation": {"x": 0, "y": 0, "z": 0},
            "rotation_degrees": {"yaw": 0, "pitch": 0, "roll": 0},
            "scale": 1,
        }
        for item in session.config()["instances"]
    }
    with pytest.raises(OSError, match="review persistence failed"):
        session.publish({"reviewer": "tester", "poses": poses})
    assert not (tmp_path / "scene").exists()
    runs = [store.get_build_run(path.stem) for path in (store.root / "runs").glob("*.json")]
    parent = next(run for run in runs if run["pipeline_name"] == "scene_layout_review")
    assert parent["status"] == "failed"
    child = ArtifactRef(**parent["node_attempts"][0]["outputs"]["child_run"])
    assert store.read_structured(child)["status"] == "succeeded"


def test_pose_validation_and_failed_publish_leave_no_layout(tmp_path):
    store, draft, _ = draft_fixture(tmp_path)
    session = SceneLayoutReviewSession(store.root, draft, tmp_path / "scene")
    with pytest.raises(ContractError, match="every draft instance"):
        session.publish({"reviewer": "tester", "poses": {}})
    assert not (tmp_path / "scene-layout.json").exists()
    with pytest.raises(ContractError, match="positive"):
        _matrix(
            {
                "translation": {"x": 0, "y": 0, "z": 0},
                "rotation_degrees": {"yaw": 0, "pitch": 0, "roll": 0},
                "scale": 0,
            }
        )


def test_existing_output_is_rejected_before_scene_construction(tmp_path, monkeypatch):
    store, draft, _ = draft_fixture(tmp_path)
    output = tmp_path / "scene"
    output.mkdir()
    session = SceneLayoutReviewSession(store.root, draft, output)
    monkeypatch.setattr(
        "assets_generator.scene_layout_review.build_scene",
        lambda **kwargs: pytest.fail("unexpected scene construction"),
    )
    with pytest.raises(FileExistsError):
        session.publish(
            {
                "reviewer": "tester",
                "poses": {
                    item["instance_id"]: {
                        "translation": {"x": 0, "y": 0, "z": 0},
                        "rotation_degrees": {"yaw": 0, "pitch": 0, "roll": 0},
                        "scale": 1,
                    }
                    for item in session.config()["instances"]
                },
            }
        )


def test_http_server_is_loopback_scoped_and_serves_fixed_models(tmp_path):
    store, draft, _ = draft_fixture(tmp_path)
    session = SceneLayoutReviewSession(store.root, draft, tmp_path / "scene")
    server = create_scene_layout_review_server(session, 0)
    assert server.server_address[0] == "127.0.0.1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port)

    def request(method, path, body=None, headers=None):
        connection.request(
            method, path, json.dumps(body) if body is not None else None, headers or {}
        )
        response = connection.getresponse()
        return response.status, response.getheader("Content-Type"), response.read()

    try:
        assert request("GET", "/")[0:2] == (200, "text/html; charset=utf-8")
        code, _, raw = request("GET", "/session")
        assert code == 200
        config = json.loads(raw)
        assert request("GET", "/models/0.glb")[0:2] == (200, "model/gltf-binary")
        assert request("GET", "/models/-1.glb")[0] == 404
        assert request("GET", "/../store")[0] == 404
        assert request("GET", "/session", headers={"Host": "evil.example"})[0] == 403
        assert request("POST", "/publish", {})[0] == 403
        headers = {
            "Origin": f"http://127.0.0.1:{server.server_port}",
            "X-Review-Token": config["token"],
        }
        poses = {
            item["instance_id"]: {
                "translation": {"x": 0, "y": 0, "z": 0},
                "rotation_degrees": {"yaw": 0, "pitch": 0, "roll": 0},
                "scale": 1,
            }
            for item in config["instances"]
        }
        code, _, raw = request("POST", "/publish", {"reviewer": "tester", "poses": poses}, headers)
        assert code == 200
        assert json.loads(raw)["layout_review"]["artifact_id"].startswith("sha256:")
        assert (
            request("POST", "/publish", {"reviewer": "tester", "poses": poses}, headers)[0] == 409
        )
    finally:
        connection.close()
        server.shutdown()
        thread.join()
        server.server_close()

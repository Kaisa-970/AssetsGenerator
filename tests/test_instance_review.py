import json
import threading
from http.client import HTTPConnection

import pytest
from PIL import Image
from test_instance_proposals import FakeProposer

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.instance_proposals import propose_instances
from assets_generator.instance_review import InstanceReviewSession, create_instance_review_server
from assets_generator.models import ArtifactRef


def fixture(tmp_path):
    image = tmp_path / "scene.png"
    Image.new("RGB", (8, 6), "red").save(image)
    result = propose_instances(
        image_path=image,
        store_path=tmp_path / "store",
        output_path=tmp_path / "proposals",
        backend=FakeProposer(),
    )
    return LocalArtifactStore(tmp_path / "store"), ArtifactRef(**result["proposals"])


def test_session_config_and_publish_use_existing_selection_boundary(tmp_path):
    store, proposals = fixture(tmp_path)
    session = InstanceReviewSession(store.root, proposals, tmp_path / "selection")
    config = session.config()
    assert config["image"] == {"url": "/image", "width": 8, "height": 6}
    assert [item["proposal_id"] for item in config["items"]] == ["p0", "p1"]
    assert [item["mask_url"] for item in config["items"]] == ["/masks/0", "/masks/1"]

    result = session.publish({"proposal_ids": ["p1", "p0"], "reviewer": " reviewer "})
    selection = store.read_structured(ArtifactRef(**result["selection"]))
    assert selection["selected_ids"] == ["p1", "p0"]
    assert selection["reviewer"] == "reviewer"
    assert session.config()["published"] == result
    manifest = json.loads((tmp_path / "selection/objects.json").read_text())
    assert [item["object_id"] for item in manifest["objects"]] == [
        "object_001",
        "object_002",
    ]


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"proposal_ids": "p0", "reviewer": "tester"}, "proposal_ids"),
        ({"proposal_ids": ["p0"], "reviewer": ""}, "reviewer"),
        ({"proposal_ids": ["missing"], "reviewer": "tester"}, "existing"),
    ],
)
def test_session_rejects_invalid_publish_input(tmp_path, payload, message):
    store, proposals = fixture(tmp_path)
    session = InstanceReviewSession(store.root, proposals, tmp_path / "selection")
    with pytest.raises(ContractError, match=message):
        session.publish(payload)
    assert not (tmp_path / "selection").exists()


def test_http_server_is_loopback_scoped_and_serves_only_review_assets(tmp_path):
    store, proposals = fixture(tmp_path)
    session = InstanceReviewSession(store.root, proposals, tmp_path / "selection")
    server = create_instance_review_server(session, 0)
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
        code, content_type, page = request("GET", "/")
        assert code == 200
        assert content_type == "text/html; charset=utf-8"
        assert b"proposal_ids:selected" in page
        code, _, raw = request("GET", "/session")
        assert code == 200
        config = json.loads(raw)
        assert request("GET", "/image")[2][:8] == b"\x89PNG\r\n\x1a\n"
        assert request("GET", "/masks/0")[2][:8] == b"\x89PNG\r\n\x1a\n"
        assert request("GET", "/masks/-1")[0] == 404
        assert request("GET", "/../store")[0] == 404
        assert request("GET", "/session", headers={"Host": "evil.example"})[0] == 403
        assert request("POST", "/publish", {"proposal_ids": ["p0"], "reviewer": "tester"})[0] == 403
        headers = {
            "Origin": f"http://127.0.0.1:{server.server_port}",
            "X-Review-Token": config["token"],
        }
        code, _, raw = request(
            "POST",
            "/publish",
            {"proposal_ids": ["p1"], "reviewer": "tester"},
            headers,
        )
        assert code == 200
        assert json.loads(raw)["manifest"].endswith("selection/objects.json")
        assert (
            request(
                "POST",
                "/publish",
                {"proposal_ids": ["p0"], "reviewer": "tester"},
                headers,
            )[0]
            == 409
        )
    finally:
        connection.close()
        server.shutdown()
        thread.join()
        server.server_close()


def test_inverted_selection_is_traceable_and_hands_off_to_extraction(tmp_path):
    import numpy as np
    from test_scene_extraction import setup

    from assets_generator.scene_extraction import extract_scene_objects

    store, proposals = fixture(tmp_path)
    original = store.read_structured(proposals)
    session = InstanceReviewSession(store.root, proposals, tmp_path / "selection")
    result = session.publish({"proposal_ids": ["p0"], "reviewer": "tester", "invert": True})
    with Image.open(tmp_path / "selection/object_001.png") as image:
        pixels = np.asarray(image)
    assert (pixels[:, :4] == 0).all()
    assert (pixels[:, 4:] == 255).all()
    assert store.read_structured(proposals) == original
    decision = store.read_structured(ArtifactRef(**result["selection"]))
    transformed = store.read_structured(ArtifactRef(**decision["proposals"]))
    assert transformed["transformation"]["source_proposals"] == {
        "artifact_id": proposals.artifact_id
    }
    assert transformed["proposals"][0]["predicted_iou"] is None
    run = store.get_build_run(result["run_id"])
    assert run["status"] == "succeeded"
    evidence = store.read_structured(
        ArtifactRef(**run["node_attempts"][0]["outputs"]["mask_provenance"])
    )
    assert evidence["parameters"]["operation"] == "invert_binary_mask"
    from assets_generator.provenance import output_id

    assert evidence["output_id"] == output_id(
        result["run_id"], "select_instances", "processed_mask"
    )
    assert evidence["run_id"] == result["run_id"]
    assert decision["scope"] == "mask_selection_and_processing"
    assert decision["processing"]["operation"] == "invert_binary_mask"
    (tmp_path / "extract").mkdir()
    args = setup(tmp_path / "extract")
    args.update(
        manifest_path=tmp_path / "selection/objects.json",
        store_path=store.root,
        output_path=tmp_path / "extracted",
    )
    extract_scene_objects(**args)


@pytest.mark.parametrize("ids,invert", [(["p0", "p1"], True), (["p0"], "yes")])
def test_invalid_inversion_is_rejected(tmp_path, ids, invert):
    store, proposals = fixture(tmp_path)
    session = InstanceReviewSession(store.root, proposals, tmp_path / "selection")
    with pytest.raises(ContractError, match="inversion"):
        session.publish({"proposal_ids": ids, "reviewer": "tester", "invert": invert})


def test_inverting_full_mask_rejects_empty_result(tmp_path):
    from assets_generator.models import StructuredValue
    from assets_generator.workflow import _import_image

    store, proposals = fixture(tmp_path)
    full = tmp_path / "full.png"
    Image.new("L", (8, 6), 255).save(full)
    mask = _import_image(store, full, "binary_mask")
    raw = store.read_structured(proposals)
    raw["proposals"][0]["mask"] = {"artifact_id": mask.artifact_id}
    changed = store.persist_structured(
        StructuredValue("instance_proposals", "InstanceProposals", "1.0", raw)
    )
    session = InstanceReviewSession(store.root, changed, tmp_path / "selection")
    before = set((store.root / "runs").glob("*.json"))
    with pytest.raises(ContractError, match="no foreground"):
        session.publish({"proposal_ids": ["p0"], "reviewer": "tester", "invert": True})
    created = set((store.root / "runs").glob("*.json")) - before
    assert len(created) == 1
    assert store.get_build_run(created.pop().stem)["status"] == "failed"
    assert not (tmp_path / "selection").exists()


@pytest.mark.parametrize("invert", [False, True])
def test_keep_largest_removes_strips_and_preserves_evidence(tmp_path, invert):
    import numpy as np

    from assets_generator.models import StructuredValue
    from assets_generator.workflow import _import_image

    store, proposals = fixture(tmp_path)
    pixels = np.zeros((6, 8), dtype=np.uint8)
    pixels[:, 0] = 255
    pixels[1:5, 3:7] = 255
    source = 255 - pixels if invert else pixels
    path = tmp_path / "components.png"
    Image.fromarray(source).save(path)
    mask = _import_image(store, path, "binary_mask")
    raw = store.read_structured(proposals)
    raw["proposals"][0]["mask"] = {"artifact_id": mask.artifact_id}
    changed = store.persist_structured(
        StructuredValue("instance_proposals", "InstanceProposals", "1.0", raw)
    )
    session = InstanceReviewSession(store.root, changed, tmp_path / "selection")
    result = session.publish(
        {"proposal_ids": ["p0"], "reviewer": "tester", "invert": invert, "keep_largest": True}
    )
    with Image.open(tmp_path / "selection/object_001.png") as image:
        actual = np.asarray(image)
    pixels[:, 0] = 0
    assert np.array_equal(actual, pixels)
    decision = store.read_structured(ArtifactRef(**result["selection"]))
    transformed = store.read_structured(ArtifactRef(**decision["proposals"]))
    assert transformed["transformation"]["removed_pixels"] == 6
    assert transformed["transformation"]["connectivity"] == 8
    assert transformed["transformation"]["invert_first"] is invert
    assert decision["scope"] == "mask_selection_and_processing"
    assert decision["processing"]["operation"] == "keep_largest_component"
    assert store.read_structured(changed) == raw

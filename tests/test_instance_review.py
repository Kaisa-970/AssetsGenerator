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

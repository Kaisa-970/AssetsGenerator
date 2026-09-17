import json
import threading
from http.client import HTTPConnection

import pytest
from test_alignment import fixture

from assets_generator.alignment_review import AlignmentReviewSession, create_review_server
from assets_generator.contracts import ContractError
from assets_generator.models import ArtifactRef


def test_save_and_immutable_decisions(tmp_path):
    store, candidate, transform = fixture(tmp_path)
    session = AlignmentReviewSession(store.root, candidate, tmp_path / "output")
    saved = session.save({"matrix": transform.matrix})
    ref = ArtifactRef(**saved["alignment"])
    original = store.blob_path(ref).read_bytes()
    for decision in ["accepted", "rejected"]:
        response = session.review(
            {
                "alignment_id": ref.artifact_id,
                "decision": decision,
                "reviewer": "tester",
                "note": "test",
            }
        )
        value = store.read_structured(ArtifactRef(**response["review"]))
        assert value == response["record"]
        assert value["candidate"]["artifact_id"] == candidate.artifact_id
        assert value["transform"] == saved["transform"]
        assert value["alignment"] == saved["alignment"]
        assert value["decision"] == decision
        assert value["scope"] == "manual_alignment_only"
    assert store.blob_path(ref).read_bytes() == original
    assert len(list((tmp_path / "output/reviews").glob("*.json"))) == 2
    assert store.get_build_run(store.read_structured(ref)["run_id"])["status"] == "succeeded"
    with pytest.raises(ContractError, match="session"):
        session.review({"alignment_id": "unknown"})
    with pytest.raises(ContractError, match="reviewer"):
        session.review({"alignment_id": ref.artifact_id, "decision": "accepted", "reviewer": " "})
    with pytest.raises(ContractError, match="decision"):
        session.review({"alignment_id": ref.artifact_id, "decision": "fused"})


def test_http_session_restricts_writes_and_paths(tmp_path):
    store, candidate, transform = fixture(tmp_path)
    session = AlignmentReviewSession(store.root, candidate, tmp_path / "output")
    server = create_review_server(session, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port)

    def request(method, path, body=None, headers=None):
        connection.request(
            method, path, json.dumps(body) if body is not None else None, headers or {}
        )
        response = connection.getresponse()
        return response.status, response.read()

    try:
        assert request("GET", "/")[0] == 200
        code, raw = request("GET", "/session")
        assert code == 200
        config = json.loads(raw)
        assert request("GET", "/models/generated.glb")[1][:4] == b"glTF"
        assert request("GET", "/../store")[0] == 404
        assert request("GET", "/session", headers={"Host": "evil.example"})[0] == 403
        assert request("POST", "/save", {"matrix": transform.matrix})[0] == 403
        headers = {
            "Origin": f"http://127.0.0.1:{server.server_port}",
            "X-Review-Token": config["token"],
        }
        assert request("POST", "/save", {"matrix": [[0]]}, headers)[0] == 400
        assert not (tmp_path / "output").exists()
        code, raw = request("POST", "/save", {"matrix": transform.matrix}, headers)
        assert code == 200
        saved = json.loads(raw)
        assert (
            request(
                "POST",
                "/review",
                {
                    "alignment_id": saved["alignment"]["artifact_id"],
                    "decision": "accepted",
                    "reviewer": "test",
                },
                headers,
            )[0]
            == 200
        )
    finally:
        connection.close()
        server.shutdown()
        thread.join()
        server.server_close()

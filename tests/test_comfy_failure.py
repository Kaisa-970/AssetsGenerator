import json

import pytest
from test_comfy_profile import profile

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.comfy_http import ComfyClient
from assets_generator.comfy_profile import ComfyImageProfile
from assets_generator.comfy_service import submit_owned_prompt
from assets_generator.comfy_submission import ComfySubmissionUnknown
from assets_generator.remote_service_store import RemoteServiceStore


@pytest.mark.parametrize("bad_history", [False, True])
def test_failed_history_publication_and_offline_recovery(tmp_path, monkeypatch, bad_history):
    raw = profile()
    raw["image_targets"] = {}
    configured = ComfyImageProfile(json.dumps(raw).encode())
    req = configured.request("one", images={}, parameters={})
    owner = RemoteServiceStore(tmp_path / "owner.sqlite", configured.identity)
    store = LocalArtifactStore(tmp_path / "store")
    inner = tmp_path / "inner.sqlite"
    owner.submit(req)
    owner.transition(req, expected="queued", state="running")
    prompt = configured.workflow().bind({})["prompt"]
    seen = []

    def transport(self, path, body=None):
        if body is not None:
            seen.append(body)
            return {"prompt_id": body["prompt_id"]}
        pid = seen[0]["prompt_id"]
        return {
            pid: {
                "prompt": [0, pid, {} if bad_history else prompt],
                "outputs": {},
                "status": {
                    "status_str": "error",
                    "completed": False,
                    "messages": [["execution_error", {"exception_message": "fixture failure"}]],
                },
            }
        }

    monkeypatch.setattr(ComfyClient, "_json", transport)
    try:
        submit_owned_prompt(
            owner,
            req,
            inner,
            ComfyClient(raw["endpoint"]),
            deployment={"endpoint": raw["endpoint"]},
            prompt=prompt,
        )
        if bad_history:
            with pytest.raises(ComfySubmissionUnknown):
                configured.finish(owner, req, inner, store)
            assert owner.lookup(req).state == "running"
            return
        job = configured.finish(owner, req, inner, store)
        assert job.state == "failed"
        error = json.loads(job.error_json)
        assert error["code"] == "COMFY_EXECUTION_FAILED"
        digest = error["detail"].split()[-1]
        assert b"fixture failure" in owner.get_blob(digest)
        monkeypatch.setattr(ComfyClient, "_json", lambda *a: pytest.fail("network on recovery"))
        inner.unlink()
        assert configured.finish(owner, req, inner, store) == job
        owner.db.execute("DELETE FROM blobs WHERE digest=?", (digest,))
        with pytest.raises(ComfySubmissionUnknown):
            configured.finish(owner, req, inner, store)
        assert len(seen) == 1
    finally:
        owner.close()

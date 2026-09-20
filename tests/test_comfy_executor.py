import json

import pytest
from test_comfy_profile import profile

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.comfy_executor import execute_next_image, recover_image_job
from assets_generator.comfy_profile import ComfyImageProfile
from assets_generator.comfy_submission import ComfySubmissionUnknown
from assets_generator.remote_protocol import RemoteRequest
from assets_generator.remote_service_store import RemoteServiceStore


def test_claim_interruption_reopen_and_recovery_never_restart(tmp_path, monkeypatch):
    configured = ComfyImageProfile(json.dumps(profile()).encode())
    path = tmp_path / "owner.sqlite"
    owner = RemoteServiceStore(path, configured.identity)
    store = LocalArtifactStore(tmp_path / "store")
    journal = tmp_path / "inner.sqlite"
    req = RemoteRequest.create(configured.identity, "one", {})
    second = RemoteRequest.create(configured.identity, "two", {})
    calls = []

    def start(self, owner, request, *args):
        calls.append(request.submission_key)
        assert owner.lookup(request).state == "running"
        raise ComfySubmissionUnknown("lost acknowledgement")

    def finish(self, owner, request, *args):
        return owner.transition(
            request,
            expected="running",
            state="failed",
            error={"code": "FIXTURE_FAILED", "detail": "fixture only"},
        )

    monkeypatch.setattr(ComfyImageProfile, "start_dag", start)
    monkeypatch.setattr(ComfyImageProfile, "finish", finish)
    owner.submit(req)
    owner.submit(second)
    with pytest.raises(ValueError, match="already claimed"):
        recover_image_job(configured, owner, "one", journal, store)
    with pytest.raises(ComfySubmissionUnknown):
        execute_next_image(configured, owner, journal, store)
    owner.close()
    owner = RemoteServiceStore(path, configured.identity)
    try:
        with pytest.raises(ValueError, match="unresolved running"):
            execute_next_image(configured, owner, journal, store)
        assert calls == ["one"]
        assert recover_image_job(configured, owner, "one", journal, store).state == "failed"
        with pytest.raises(ComfySubmissionUnknown):
            execute_next_image(configured, owner, journal, store)
        assert calls == ["one", "two"]
    finally:
        owner.close()


def test_profile_mismatch_does_not_claim_queue(tmp_path):
    raw = profile()
    configured = ComfyImageProfile(json.dumps(raw).encode())
    owner = RemoteServiceStore(tmp_path / "owner.sqlite", configured.identity)
    req = RemoteRequest.create(configured.identity, "one", {})
    owner.submit(req)
    raw["defaults"] = {"seed": 2}
    changed = ComfyImageProfile(json.dumps(raw).encode())
    try:
        with pytest.raises(ValueError, match="identity"):
            execute_next_image(
                changed, owner, tmp_path / "journal", LocalArtifactStore(tmp_path / "store")
            )
        assert owner.lookup(req).state == "queued"
    finally:
        owner.close()

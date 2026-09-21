import json
from threading import Event

import pytest
from test_sam3d_bridge import setup as bridge_setup  # noqa: F401

from assets_generator.remote_protocol import RemoteRequest
from assets_generator.sam3d_service_loop import run_loop, tick
from assets_generator.sam3d_service_store import Sam3DServiceStore


def test_loop_recovers_lost_response_after_reopen_then_drains_queue(bridge_setup, tmp_path):  # noqa: F811
    owner, request, upstream, bridge, artifacts = bridge_setup
    upstream.drop = True
    assert tick(owner, bridge, artifacts)["state"] == "uncertain"
    assert upstream.posts == 1
    next_request = RemoteRequest.create(owner.identity, "next", json.loads(request.payload_json))
    owner.submit(next_request)
    reopened = Sam3DServiceStore(tmp_path / "service.sqlite", owner.identity)
    assert tick(reopened, bridge, artifacts)["state"] == "running"
    assert upstream.posts == 1
    upstream.complete()
    assert tick(reopened, bridge, artifacts)["state"] == "succeeded"
    upstream.status = "running"
    upstream.drop = False
    assert tick(reopened, bridge, artifacts)["state"] == "running"
    assert upstream.posts == 2
    reopened.close()


def test_unknown_original_blocks_later_jobs_and_missing_evidence_stops(bridge_setup):  # noqa: F811
    owner, request, upstream, bridge, artifacts = bridge_setup
    assert tick(owner, bridge, artifacts)["state"] == "running"
    upstream.job = None
    owner.submit(RemoteRequest.create(owner.identity, "next", json.loads(request.payload_json)))
    for _ in range(3):
        assert tick(owner, bridge, artifacts)["state"] == "uncertain"
    assert upstream.posts == 1
    owner.db.execute("DELETE FROM sam3d_jobs")
    with pytest.raises(ValueError, match="ownership missing"):
        tick(owner, bridge, artifacts)
    assert upstream.posts == 1


def test_worker_finds_old_running_job_beyond_first_page(bridge_setup):  # noqa: F811
    owner, request, upstream, bridge, artifacts = bridge_setup
    tick(owner, bridge, artifacts)
    for index in range(105):
        owner.submit(
            RemoteRequest.create(
                owner.identity, f"queued-{index}", json.loads(request.payload_json)
            )
        )
    assert tick(owner, bridge, artifacts)["state"] == "running"
    assert upstream.posts == 1


def test_stop_signal_does_not_claim_or_resubmit(bridge_setup):  # noqa: F811
    owner, request, upstream, bridge, artifacts = bridge_setup
    stop = Event()
    stop.set()
    run_loop(owner, bridge, artifacts, stop=stop)
    assert owner.lookup(request).state == "queued" and upstream.posts == 0
    stop.clear()
    messages = []

    def report(message):
        messages.append(message)
        stop.set()

    run_loop(owner, bridge, artifacts, stop=stop, interval=0.1, report=report)
    assert len(messages) == 1 and upstream.posts == 1
    assert owner.lookup(request).state == "running"


def test_cli_worker_lock_excludes_second_worker(bridge_setup, tmp_path, monkeypatch):  # noqa: F811
    import fcntl
    import sys

    from assets_generator.sam3d_service_cli import main
    from assets_generator.serialization import canonical_json_bytes

    owner, request, upstream, bridge, artifacts = bridge_setup
    deployment = tmp_path / "deployment.json"
    deployment.write_bytes(canonical_json_bytes(upstream.deployment))
    monkeypatch.setenv("SAM3D_API_KEY", "test-key")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sam3d-service",
            "work",
            "--directory",
            str(tmp_path),
            "--deployment",
            str(deployment),
            "--endpoint",
            upstream.endpoint,
        ],
    )
    with (tmp_path / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="already running"):
            main()
    assert owner.lookup(request).state == "queued" and upstream.posts == 0

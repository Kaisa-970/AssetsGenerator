import json
import sys

import pytest
from test_remote_http import request

from assets_generator.remote_protocol import RemoteRequest
from assets_generator.remote_service_process import ServiceProcessWorker
from assets_generator.remote_service_store import RemoteServiceStore
from assets_generator.worker import ProcessJobRequest


def test_service_process_records_exit_and_rejects_replay(tmp_path):
    owner = request()
    store = RemoteServiceStore(tmp_path / "service.sqlite", owner.identity)
    try:
        store.submit(owner)
        store.transition(owner, expected="queued", state="running")
        command = ProcessJobRequest(
            [sys.executable, "-c", "print('worker-ok')"], tmp_path, 10, "test"
        )
        result = ServiceProcessWorker(store, owner).run(command)
        assert result.stdout.strip() == "worker-ok"
        record = json.loads(store.worker_record(owner))
        assert record["launch_phase"] == "exit_observed"
        assert record["exit_code"] == 0
        assert record["last_probe"]["result"] == "exited"
        with pytest.raises(ValueError, match="revision conflict"):
            ServiceProcessWorker(store, owner).run(command)
    finally:
        store.close()


def test_authorization_failure_never_executes_and_blocks_new_job(tmp_path, monkeypatch):
    owner = request()
    path = tmp_path / "service.sqlite"
    store = RemoteServiceStore(path, owner.identity)
    store.submit(owner)
    store.transition(owner, expected="queued", state="running")
    marker = tmp_path / "executed"
    command = ProcessJobRequest(
        [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"], tmp_path, 10, "test"
    )
    original = store.save_worker

    def fail(req, body, **kwargs):
        if json.loads(body)["launch_phase"] == "release_authorized":
            raise OSError("authorization storage failed")
        original(req, body, **kwargs)

    monkeypatch.setattr(store, "save_worker", fail)
    with pytest.raises(OSError):
        ServiceProcessWorker(store, owner).run(command)
    assert not marker.exists()
    store.close()
    store = RemoteServiceStore(path, owner.identity)
    try:
        other = RemoteRequest.create(owner.identity, "another", {})
        store.submit(other)
        store.transition(other, expected="queued", state="running")
        with pytest.raises(ValueError, match="occupied"):
            ServiceProcessWorker(store, other).run(command)
        assert not marker.exists()
        assert store.observe_worker(owner).result == "exited"
        old = json.loads(store.worker_record(owner))
        assert old["exit_code"] is None
        assert old["launch_phase"] == "identity_recorded"
        assert ServiceProcessWorker(store, other).run(command).status == "succeeded"
        assert marker.exists()
    finally:
        store.close()


@pytest.mark.parametrize("result", ["alive", "unknown"])
def test_unverified_observation_does_not_release_slot(tmp_path, monkeypatch, result):
    from assets_generator.serialization import canonical_json_bytes
    from assets_generator.workbench_models import ProcessObservation, WorkerExecution
    from assets_generator.workbench_process import LinuxProcessProbe

    owner = request()
    store = RemoteServiceStore(tmp_path / "service.sqlite", owner.identity)
    try:
        store.submit(owner)
        store.transition(owner, expected="queued", state="running")
        worker = WorkerExecution("worker", owner.submission_key, "sha256:" + "a" * 64)
        before = canonical_json_bytes(worker)
        store.save_worker(owner, before, previous=None)
        worker.host_id, worker.boot_id = "host", "boot"
        worker.pid, worker.pgid, worker.starttime_ticks = 123, 123, 456
        worker.launch_phase = "identity_recorded"
        store.save_worker(owner, canonical_json_bytes(worker), previous=before)
        monkeypatch.setattr(
            LinuxProcessProbe, "observe", lambda *_: ProcessObservation("now", result)
        )
        assert store.observe_worker(owner).result == result
        assert store.db.execute("SELECT COUNT(*) FROM process_exits").fetchone()[0] == 0
        other = RemoteRequest.create(owner.identity, "other", {})
        store.submit(other)
        store.transition(other, expected="queued", state="running")
        with pytest.raises(ValueError, match="occupied"):
            store.save_worker(
                other,
                canonical_json_bytes(WorkerExecution("other", "other", "digest")),
                previous=None,
            )
    finally:
        store.close()


def test_handler_preserves_backend_failure_after_verified_exit(tmp_path):
    from assets_generator.remote_service_worker import execute_service_job

    owner = request()
    store = RemoteServiceStore(tmp_path / "service.sqlite", owner.identity)
    try:
        store.submit(owner)

        def handler(req, service):
            ServiceProcessWorker(service, req).run(
                ProcessJobRequest(
                    [sys.executable, "-c", "raise SystemExit(7)"], tmp_path, 10, "fail"
                )
            )
            return {}

        job = execute_service_job(store, owner, handler)
        assert job.state == "failed"
        assert json.loads(job.error_json)["code"] == "backend_failed"
        assert json.loads(store.worker_record(owner))["exit_code"] == 7
    finally:
        store.close()


@pytest.mark.parametrize("terminal", ["succeeded", "failed"])
def test_unobserved_process_cannot_become_terminal(tmp_path, terminal):
    from assets_generator.serialization import canonical_json_bytes
    from assets_generator.workbench_models import WorkerExecution

    owner = request()
    store = RemoteServiceStore(tmp_path / "service.sqlite", owner.identity)
    try:
        store.submit(owner)
        store.transition(owner, expected="queued", state="running")
        record = WorkerExecution("worker", owner.submission_key, "digest")
        store.save_worker(owner, canonical_json_bytes(record), previous=None)
        with pytest.raises(ValueError, match="uncertain"):
            store.transition(
                owner,
                expected="running",
                state=terminal,
                result={"outputs": []} if terminal == "succeeded" else None,
                error={"code": "backend_timeout", "detail": "unknown group"}
                if terminal == "failed"
                else None,
            )
        assert store.lookup(owner).state == "running"
    finally:
        store.close()

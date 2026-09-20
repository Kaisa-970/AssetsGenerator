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
    finally:
        store.close()

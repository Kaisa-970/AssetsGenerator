import threading

import pytest
from test_remote_http import request

from assets_generator.errors import ServiceExecutionUncertain
from assets_generator.remote_protocol import RemoteRequest
from assets_generator.remote_service_store import RemoteServiceStore
from assets_generator.remote_service_worker import ServiceOutput
from assets_generator.remote_shape_loop import run_loop


def test_known_failure_continues_and_reopen_does_not_reexecute(tmp_path):
    stop = threading.Event()
    store = RemoteServiceStore(tmp_path / "db", request().identity)
    bad = RemoteRequest.create(store.identity, "bad", {})
    good = RemoteRequest.create(store.identity, "good", {})
    calls = []
    for req in (bad, good):
        store.submit(req)

    def handler(req, owner):
        calls.append(req.submission_key)
        if req == bad:
            raise ValueError("bad input")
        stop.set()
        return {"result": ServiceOutput(b"mesh", "application/octet-stream")}

    try:
        run_loop(store, handler, stop=stop)
        assert calls == ["bad", "good"]
        assert store.lookup(bad).state == "failed"
        assert store.lookup(good).state == "succeeded"
    finally:
        store.close()
    restored = RemoteServiceStore(tmp_path / "db", request().identity)

    class IdleStop:
        def is_set(self):
            return False

        def wait(self, interval):
            raise InterruptedError("empty queue")

    try:
        with pytest.raises(InterruptedError, match="empty queue"):
            run_loop(restored, handler, stop=IdleStop())
        assert calls == ["bad", "good"]
    finally:
        restored.close()


def test_uncertain_execution_stops_loop_and_reopen_blocks_next(tmp_path):
    store = RemoteServiceStore(tmp_path / "db", request().identity)
    first = RemoteRequest.create(store.identity, "first", {})
    second = RemoteRequest.create(store.identity, "second", {})
    for req in (first, second):
        store.submit(req)
    calls = []

    def handler(req, owner):
        calls.append(req.submission_key)
        raise ServiceExecutionUncertain("unknown worker")

    try:
        with pytest.raises(ServiceExecutionUncertain):
            run_loop(store, handler, stop=threading.Event())
        assert store.lookup(first).state == "running"
    finally:
        store.close()
    restored = RemoteServiceStore(tmp_path / "db", request().identity)
    try:
        with pytest.raises(ValueError, match="running"):
            run_loop(restored, handler, stop=threading.Event())
        assert calls == ["first"]
        assert restored.lookup(second).state == "queued"
    finally:
        restored.close()


@pytest.mark.parametrize("mode", ["changed", "unreadable"])
def test_deployment_invalid_fails_current_and_leaves_next_queued(tmp_path, mode):
    import json

    from assets_generator.errors import DeploymentIdentityError
    from assets_generator.remote_protocol import RemoteIdentity
    from assets_generator.remote_shape_service import ShapeServiceHandler

    identity = request().identity
    store = RemoteServiceStore(tmp_path / "db", identity)
    payload = {
        "operation": "shape_generation@1",
        "parameters": {"seed": 42, "pipeline_type": "512"},
        "input_blobs": {},
        "input_digest": "unused",
        "binding_digest": "unused",
    }
    first = RemoteRequest.create(identity, "first", payload)
    second = RemoteRequest.create(identity, "second", payload)
    calls = []

    def verify():
        calls.append(True)
        if mode == "unreadable":
            raise ValueError("weights changed")
        return RemoteIdentity(identity.service_id, "sha256:" + "f" * 64)

    handler = ShapeServiceHandler(
        identity, tmp_path / "work", lambda worker: pytest.fail("inference started"), verify
    )
    for req in (first, second):
        store.submit(req)
    try:
        with pytest.raises(DeploymentIdentityError, match="administrator"):
            run_loop(store, handler, stop=threading.Event())
        failed = store.lookup(first)
        assert failed.state == "failed"
        assert json.loads(failed.error_json)["code"] == "deployment_invalid"
        assert store.lookup(second).state == "queued"
        assert calls == [True]
    finally:
        store.close()

import threading

import pytest
from test_remote_http import request

from assets_generator.remote_protocol import RemoteRequest
from assets_generator.remote_service_store import RemoteServiceStore
from assets_generator.remote_service_worker import (
    ServiceOutput,
    execute_next_service_job,
    execute_service_job,
)


def test_queue_claim_is_serial_across_connections_and_preserves_interrupted_jobs(tmp_path):
    identity = request().identity
    first = RemoteServiceStore(tmp_path / "db", identity)
    second = RemoteServiceStore(tmp_path / "db", identity)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def handler(req, store):
        calls.append(req.submission_key)
        entered.set()
        assert release.wait(10)
        return {"result": ServiceOutput(b"done", "application/octet-stream")}

    try:
        for key in ("first", "second"):
            first.submit(RemoteRequest.create(identity, key, {}))
        thread = threading.Thread(target=execute_next_service_job, args=(first, handler))
        thread.start()
        assert entered.wait(5)
        with pytest.raises(ValueError, match="unresolved running"):
            execute_next_service_job(second, handler)
        with pytest.raises(ValueError, match="unresolved running"):
            execute_service_job(second, RemoteRequest.create(identity, "second", {}), handler)
        assert second.lookup(RemoteRequest.create(identity, "second", {})).state == "queued"
        assert calls == ["first"]
        release.set()
        thread.join(10)
        assert not thread.is_alive()
        assert execute_next_service_job(second, handler).state == "succeeded"
        assert execute_next_service_job(first, handler) is None
        assert calls == ["first", "second"]
        first.submit(RemoteRequest.create(identity, "interrupted", {}))
        assert first.claim_next_queued().submission_key == "interrupted"
        first.submit(RemoteRequest.create(identity, "later", {}))
        with pytest.raises(ValueError, match="unresolved running"):
            second.claim_next_queued()
        assert second.lookup(RemoteRequest.create(identity, "later", {})).state == "queued"
    finally:
        release.set()
        first.close()
        second.close()


def test_explicit_job_blocks_queue_without_consuming_other_request(tmp_path):
    identity = request().identity
    store = RemoteServiceStore(tmp_path / "db", identity)
    a = RemoteRequest.create(identity, "a", {})
    b = RemoteRequest.create(identity, "b", {})
    try:
        store.submit(a)
        store.submit(b)

        def interrupted(req, current):
            with pytest.raises(ValueError, match="unresolved running"):
                execute_next_service_job(current, lambda *args: {})
            raise KeyboardInterrupt()

        with pytest.raises(KeyboardInterrupt):
            execute_service_job(store, b, interrupted)
        assert store.lookup(a).state == "queued"
        assert store.lookup(b).state == "running"
    finally:
        store.close()

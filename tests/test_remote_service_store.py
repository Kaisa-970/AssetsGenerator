from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from test_remote_http import request

from assets_generator.remote_service_store import RemoteServiceStore
from assets_generator.serialization import sha256_bytes


def test_restart_preserves_claim_terminal_and_blobs(tmp_path):
    req = request()
    path = tmp_path / "service.sqlite"
    store = RemoteServiceStore(path, req.identity)
    store.put_blob(b"image", sha256_bytes(b"image"))
    assert store.submit(req).state == "queued"
    store.transition(req, expected="queued", state="running")
    store.close()
    store = RemoteServiceStore(path, req.identity)
    assert store.get_blob(sha256_bytes(b"image")) == b"image"
    assert store.submit(req).state == "running"
    with pytest.raises(ValueError, match="state conflict"):
        store.transition(req, expected="queued", state="running")
    terminal = store.transition(
        req,
        expected="running",
        state="failed",
        error={"code": "MODEL_FAILED", "detail": "test failure"},
    )
    store.close()
    store = RemoteServiceStore(path, req.identity)
    assert store.lookup(req) == terminal
    assert store.submit(req) == terminal
    with pytest.raises(ValueError, match="state conflict"):
        store.transition(req, expected="running", state="succeeded", result={"outputs": []})
    with pytest.raises(ValueError, match="key conflict"):
        store.submit(request({"different": True}))
    store.close()
    with pytest.raises(ValueError, match="identity"):
        RemoteServiceStore(path, replace(req.identity, service_id="another"))


def test_concurrent_connections_claim_once(tmp_path):
    req = request()
    path = tmp_path / "service.sqlite"
    stores = [RemoteServiceStore(path, req.identity) for _ in range(2)]

    def claim(store):
        store.submit(req)
        try:
            store.transition(req, expected="queued", state="running")
            return True
        except ValueError:
            return False

    try:
        with ThreadPoolExecutor(2) as pool:
            assert sorted(pool.map(claim, stores)) == [False, True]
        assert all(store.lookup(req).state == "running" for store in stores)
    finally:
        for store in stores:
            store.close()


def test_invalid_terminal_rolls_back_and_success_is_immutable(tmp_path):
    req = request()
    store = RemoteServiceStore(tmp_path / "service.sqlite", req.identity)
    try:
        store.submit(req)
        store.transition(req, expected="queued", state="running")
        with pytest.raises(ValueError):
            store.transition(req, expected="running", state="succeeded")
        assert store.lookup(req).state == "running"
        terminal = store.transition(
            req, expected="running", state="succeeded", result={"outputs": []}
        )
        with pytest.raises(ValueError, match="state conflict"):
            store.transition(
                req, expected="running", state="failed", error={"code": "FAIL", "detail": "changed"}
            )
        assert store.submit(req) == terminal
        with pytest.raises(ValueError, match="invalid service input"):
            store.put_blob(b"changed", sha256_bytes(b"original"))
        with pytest.raises(ValueError, match="missing/corrupt"):
            store.get_blob(sha256_bytes(b"original"))
    finally:
        store.close()

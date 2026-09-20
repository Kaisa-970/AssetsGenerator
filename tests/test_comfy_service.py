import shutil

import pytest
from test_remote_http import request

from assets_generator.comfy_http import ComfyClient
from assets_generator.comfy_service import recover_owned_prompt, submit_owned_prompt
from assets_generator.comfy_submission import ComfySubmissionJournal, ComfySubmissionUnknown
from assets_generator.remote_service_store import RemoteServiceStore


@pytest.mark.parametrize("damage", ["none", "directory", "row", "rollback"])
def test_owned_submission_recovery_never_reposts(tmp_path, monkeypatch, damage):
    req = request()
    path = tmp_path / "service.sqlite"
    inner = tmp_path / "inner" / "journal.sqlite"
    store = RemoteServiceStore(path, req.identity)
    store.submit(req)
    store.transition(req, expected="queued", state="running")
    client = ComfyClient("http://127.0.0.1:8188")
    calls = []
    prompt = {"1": {"class_type": "Example", "inputs": {}}}

    def transport(path, body=None):
        calls.append(path)
        if path == "/prompt":
            assert store.comfy_binding(req)["prompt_id"] == body["prompt_id"]
            raise ConnectionError("upstream accepted; lost ack")
        prompt_id = path.rsplit("/", 1)[1]
        return {
            prompt_id: {
                "prompt": [1, prompt_id, prompt, {}, []],
                "outputs": {},
                "status": {"status_str": "success", "completed": True, "messages": []},
            }
        }

    monkeypatch.setattr(client, "_json", transport)
    try:
        with pytest.raises(ComfySubmissionUnknown):
            submit_owned_prompt(
                store, req, inner, client, deployment={"endpoint": client.endpoint}, prompt=prompt
            )
        binding = store.comfy_binding(req)
    finally:
        store.close()
    if damage == "directory":
        shutil.rmtree(inner.parent)
    elif damage in {"row", "rollback"}:
        journal = ComfySubmissionJournal(inner)
        journal.db.execute(
            "DELETE FROM prompts" if damage == "row" else "UPDATE prompts SET phase='prepared'"
        )
        journal.close()
    store = RemoteServiceStore(path, req.identity)
    try:
        assert store.comfy_binding(req) == binding
        if damage == "none":
            result = recover_owned_prompt(store, req, inner, client)
            assert result["provenance_scope"] == "composite_boundary_only"
            with pytest.raises(ComfySubmissionUnknown):
                submit_owned_prompt(store, req, inner, client, deployment={}, prompt={})
        else:
            with pytest.raises(ComfySubmissionUnknown):
                recover_owned_prompt(store, req, inner, client)
        assert calls.count("/prompt") == 1
        assert store.lookup(req).state == "running"
        if damage == "directory":
            assert not inner.parent.exists()
    finally:
        store.close()


def test_only_one_outer_connection_authorizes_post(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    req = request()
    path = tmp_path / "service.sqlite"
    stores = [RemoteServiceStore(path, req.identity) for _ in range(2)]
    journal = ComfySubmissionJournal(tmp_path / "comfy.sqlite")
    try:
        stores[0].submit(req)
        stores[0].transition(req, expected="queued", state="running")
        journal.prepare(req.submission_key, deployment={"revision": "test"}, prompt={"1": {}})
        binding = journal.submission_binding(req.submission_key)
        with ThreadPoolExecutor(2) as pool:
            outcomes = list(pool.map(lambda s: s.authorize_comfy_submission(req, binding), stores))
        assert sorted(outcomes) == [False, True]
        assert all(s.comfy_binding(req) == binding for s in stores)
        changed = {**binding, "request_digest": "sha256:" + "0" * 64}
        with pytest.raises(ValueError, match="conflict"):
            stores[0].authorize_comfy_submission(req, changed)
        assert stores[0].comfy_binding(req) == binding
    finally:
        journal.close()
        for store in stores:
            store.close()


def test_existing_service_jobs_survive_binding_column_upgrade(tmp_path):
    import sqlite3

    req = request()
    path = tmp_path / "service.sqlite"
    store = RemoteServiceStore(path, req.identity)
    before = store.submit(req)
    store.close()
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE jobs DROP COLUMN comfy_binding")
    store = RemoteServiceStore(path, req.identity)
    try:
        assert store.lookup(req) == before
        assert store.comfy_binding(req) is None
        assert store.submit(req) == before
    finally:
        store.close()

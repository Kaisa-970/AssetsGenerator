import io

import pytest
from PIL import Image
from test_remote_http import request

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.comfy_http import ComfyClient
from assets_generator.comfy_service import finish_owned_image, start_owned_image
from assets_generator.comfy_submission import ComfySubmissionJournal, ComfySubmissionUnknown
from assets_generator.comfy_workflow import ComfyWorkflow
from assets_generator.dag_adapters import AdapterSpec
from assets_generator.remote_service_store import RemoteServiceStore


@pytest.mark.parametrize("damage", ["none", "receipt", "blob"])
def test_owned_completion_recovery_does_not_repeat_import(tmp_path, monkeypatch, damage):
    req = request()
    path = tmp_path / "service.sqlite"
    journal_path = tmp_path / "journal.sqlite"
    store = LocalArtifactStore(tmp_path / "store")
    owner = RemoteServiceStore(path, req.identity)
    client = ComfyClient("http://127.0.0.1:8188")
    owner.submit(req)
    owner.transition(req, expected="queued", state="running")
    workflow = ComfyWorkflow(
        {"1": {"class_type": "Fixture", "inputs": {}}},
        AdapterSpec("fixture", "1", ("fixture@1",), {"type": "object", "properties": {}}),
        {},
    )
    posts, downloads = [], []

    def transport(path, body=None):
        if body is not None:
            posts.append(body)
            return {"prompt_id": body["prompt_id"]}
        prompt_id = posts[0]["prompt_id"]
        return {
            prompt_id: {
                "prompt": [0, prompt_id, workflow.bind({})["prompt"]],
                "outputs": {
                    "1": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}
                },
                "status": {"status_str": "success", "completed": True, "messages": []},
            }
        }

    png = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(png, format="PNG")

    def download(*args, **kwargs):
        downloads.append(True)
        return png.getvalue()

    monkeypatch.setattr(client, "_json", transport)
    monkeypatch.setattr(client, "download_image", download)
    start_owned_image(
        owner,
        req,
        journal_path,
        client,
        store,
        workflow,
        images={},
        parameters={},
        deployment_claims={"fixture": True},
    )
    with pytest.raises(ComfySubmissionUnknown):
        start_owned_image(
            owner,
            req,
            journal_path,
            client,
            store,
            workflow,
            images={},
            parameters={},
            deployment_claims={"fixture": True},
        )
    # Interrupt after output import, before reserving the boundary evidence.
    import assets_generator.comfy_result as result_module

    original = result_module.fix_image_result
    monkeypatch.setattr(
        result_module, "fix_image_result", lambda *a, **k: (_ for _ in ()).throw(OSError("crash"))
    )
    with pytest.raises(ComfySubmissionUnknown):
        finish_owned_image(owner, req, journal_path, client, store, node="1", index=0, mode="RGB")
    owner.close()
    if damage == "receipt":
        journal = ComfySubmissionJournal(journal_path)
        journal.db.execute("DELETE FROM image_imports")
        journal.close()
    elif damage == "blob":
        from assets_generator.artifact_store import _digest_path
        from assets_generator.serialization import sha256_bytes

        _digest_path(store.blobs_dir, sha256_bytes(png.getvalue())).unlink()
    owner = RemoteServiceStore(path, req.identity)
    monkeypatch.setattr(result_module, "fix_image_result", original)
    try:
        if damage == "none":
            ref = finish_owned_image(
                owner, req, journal_path, client, store, node="1", index=0, mode="RGB"
            )
            monkeypatch.setattr(
                client, "_json", lambda *a, **k: pytest.fail("network during offline recovery")
            )
            assert (
                finish_owned_image(
                    owner, req, journal_path, client, store, node="1", index=0, mode="RGB"
                )
                == ref
            )
            assert store.read_structured(ref)["submission"] == owner.comfy_binding(req)
            from assets_generator.comfy_result import publish_image_result

            # Simulate failure after both blobs are inserted but before terminal commit.
            owner.db.execute(
                "CREATE TRIGGER reject_publish BEFORE UPDATE OF job ON jobs "
                "BEGIN SELECT RAISE(ABORT, 'injected publish crash'); END"
            )
            with pytest.raises(ComfySubmissionUnknown):
                publish_image_result(owner, req, store)
            assert owner.lookup(req).state == "running"
            assert owner.db.execute("SELECT count(*) FROM blobs").fetchone()[0] == 0
            owner.db.execute("DROP TRIGGER reject_publish")
            terminal = publish_image_result(owner, req, store)
            assert terminal.state == "succeeded"
            owner.close()
            owner = RemoteServiceStore(path, req.identity)
            assert publish_image_result(owner, req, store) == terminal
            descriptor, data = owner.download(req, "image")
            assert data == png.getvalue()
            assert owner.download(req, "evidence")[1] == store.blob_path(ref).read_bytes()
            owner.db.execute("DELETE FROM blobs WHERE digest=?", (descriptor.blob_digest,))
            with pytest.raises(ComfySubmissionUnknown):
                publish_image_result(owner, req, store)
            assert (
                owner.db.execute(
                    "SELECT count(*) FROM blobs WHERE digest=?", (descriptor.blob_digest,)
                ).fetchone()[0]
                == 0
            )
        else:
            with pytest.raises(ComfySubmissionUnknown):
                finish_owned_image(
                    owner, req, journal_path, client, store, node="1", index=0, mode="RGB"
                )
        assert len(posts) == len(downloads) == 1
        assert owner.lookup(req).state == ("succeeded" if damage == "none" else "running")
    finally:
        owner.close()

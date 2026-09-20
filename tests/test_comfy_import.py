import io

import pytest
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.comfy_http import ComfyClient
from assets_generator.comfy_import import import_image
from assets_generator.comfy_submission import ComfySubmissionJournal


@pytest.mark.parametrize("crash", ["none", "before_store", "after_store"])
def test_import_recovery_never_redownloads_or_repairs(tmp_path, monkeypatch, crash):
    path = tmp_path / "journal.sqlite"
    journal = ComfySubmissionJournal(path)
    client = ComfyClient("http://localhost:8188")
    store = LocalArtifactStore(tmp_path / "store")
    prompt = {"1": {"class_type": "Fixture", "inputs": {}}}
    record = journal.prepare("one", deployment={"endpoint": client.endpoint}, prompt=prompt)
    journal.submit_once("one", lambda body: {"prompt_id": body["prompt_id"]})
    journal.record_history(
        "one",
        {
            record["prompt_id"]: {
                "prompt": [0, record["prompt_id"], prompt],
                "outputs": {
                    "1": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}
                },
                "status": {"status_str": "success", "completed": True, "messages": []},
            }
        },
    )
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(stream, format="PNG")
    monkeypatch.setattr(client, "download_image", lambda *args, **kwargs: stream.getvalue())
    original = store.persist_bytes
    refs = []

    def interrupted(*args, **kwargs):
        if crash == "before_store":
            raise OSError("injected before Store")
        refs.append(original(*args, **kwargs))
        raise OSError("injected after Store")

    if crash != "none":
        monkeypatch.setattr(store, "persist_bytes", interrupted)
        with pytest.raises(OSError, match="injected"):
            import_image(client, journal, store, "one", node="1", index=0, mode="RGB")
    else:
        refs.append(import_image(client, journal, store, "one", node="1", index=0, mode="RGB"))
    journal.close()
    journal = ComfySubmissionJournal(path)
    monkeypatch.setattr(client, "download_image", lambda *a, **k: pytest.fail("redownloaded"))
    monkeypatch.setattr(store, "persist_bytes", lambda *a, **k: pytest.fail("repaired"))
    try:
        if crash == "before_store":
            with pytest.raises(ValueError, match="missing or corrupt"):
                import_image(client, journal, store, "one", node="1", index=0, mode="RGB")
        else:
            assert (
                import_image(client, journal, store, "one", node="1", index=0, mode="RGB")
                == refs[0]
            )
            with pytest.raises(ValueError, match="binding conflict"):
                import_image(client, journal, store, "one", node="1", index=0, mode="RGBA")
            store.blob_path(refs[0]).unlink()
            with pytest.raises(ValueError, match="missing or corrupt"):
                import_image(client, journal, store, "one", node="1", index=0, mode="RGB")
    finally:
        journal.close()

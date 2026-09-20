import io

import pytest
from PIL import Image
from test_remote_http import request

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.comfy_http import ComfyClient
from assets_generator.comfy_service import start_owned_image
from assets_generator.comfy_submission import ComfySubmissionUnknown
from assets_generator.comfy_workflow import ComfyWorkflow
from assets_generator.dag_adapters import AdapterSpec
from assets_generator.remote_service_store import RemoteServiceStore


@pytest.mark.parametrize("failure", ["upload", "after_upload"])
def test_start_intent_survives_interruption_before_prompt_binding(tmp_path, monkeypatch, failure):
    import assets_generator.comfy_service as service_module
    import assets_generator.comfy_upload as upload_module

    req = request()
    path = tmp_path / "owner.sqlite"
    owner = RemoteServiceStore(path, req.identity)
    owner.submit(req)
    owner.transition(req, expected="queued", state="running")
    store = LocalArtifactStore(tmp_path / "store")
    png = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(png, format="PNG")
    source = store.persist_bytes(
        png.getvalue(),
        kind="rgb_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png"},
    )
    workflow = ComfyWorkflow(
        {"1": {"class_type": "LoadImage", "inputs": {"image": "unused"}}},
        AdapterSpec("fixture", "1", ("fixture@1",), {"type": "object", "properties": {}}),
        {},
        image_targets={"image": ("1", "image")},
    )
    client = ComfyClient("http://127.0.0.1:8188")
    calls = []

    def upload(*args):
        assert owner.db.execute("SELECT comfy_start FROM jobs").fetchone()[0] is not None
        calls.append("upload")
        if failure == "upload":
            raise OSError("lost upload acknowledgement")
        blob = store.get_manifest(source.artifact_id).identity.blob_digest
        filename = "asset-" + blob[7:] + ".png"
        return {
            "artifact_id": source.artifact_id,
            "blob_digest": blob,
            "endpoint": client.endpoint,
            "filename": filename,
            "subfolder": "assets-generator",
            "type": "input",
            "workflow_value": "assets-generator/" + filename,
            "verification": "exact-byte-readback@1",
        }

    def before_submission(*args, **kwargs):
        raise OSError("crash after upload before submission binding")

    monkeypatch.setattr(upload_module, "upload_image", upload)
    monkeypatch.setattr(service_module, "submit_owned_prompt", before_submission)

    def start():
        return start_owned_image(
            owner,
            req,
            tmp_path / "inner.sqlite",
            client,
            store,
            workflow,
            images={"image": source},
            parameters={},
            deployment_claims={},
        )

    try:
        with pytest.raises(ComfySubmissionUnknown):
            start()
        assert owner.comfy_binding(req) is None
    finally:
        owner.close()
    owner = RemoteServiceStore(path, req.identity)
    try:
        with pytest.raises(ComfySubmissionUnknown):
            start()
        assert calls == ["upload"]
        assert owner.lookup(req).state == "running"
        assert owner.comfy_binding(req) is None
    finally:
        owner.close()

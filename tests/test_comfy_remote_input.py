import io

import pytest
from PIL import Image
from test_remote_http import request

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.comfy_remote_input import import_remote_image
from assets_generator.remote_protocol import RemoteRequest
from assets_generator.remote_service_store import RemoteServiceStore
from assets_generator.serialization import canonical_json_bytes, sha256_bytes


@pytest.mark.parametrize("damage", [None, "artifact_id", "kind", "encoding", "blob"])
def test_dag_image_import_identity_and_read_only_recovery(tmp_path, monkeypatch, damage):
    buffer = io.BytesIO()
    Image.new("RGB", (2, 3), "blue").save(buffer, format="PNG")
    data = buffer.getvalue() if damage != "encoding" else b"not a PNG"
    identity = {
        "kind": "rgba_image" if damage == "kind" else "rgb_image",
        "schema_name": "png",
        "schema_version": "1.0",
        "blob_digest": sha256_bytes(data),
        "identity_metadata": {"media_type": "image/png"},
    }
    artifact_id = sha256_bytes(canonical_json_bytes(identity))
    payload = {
        "operation": "image_transform@1",
        "parameters": {},
        "input_digest": "sha256:" + "1" * 64,
        "binding_digest": "sha256:" + "2" * 64,
        "input_blobs": {
            "image": {
                "artifact_id": "wrong" if damage == "artifact_id" else artifact_id,
                "identity": identity,
            }
        },
    }
    req = RemoteRequest.create(request().identity, "one", payload)
    owner = RemoteServiceStore(tmp_path / "service.sqlite", req.identity)
    store = LocalArtifactStore(tmp_path / "store")
    try:
        owner.submit(req)
        if damage != "blob":
            owner.put_blob(data, identity["blob_digest"])
        if damage:
            with pytest.raises(ValueError):
                import_remote_image(req, owner, store)
            assert not list(store.manifests_dir.rglob("*.json"))
            return
        ref = import_remote_image(req, owner, store)
        assert ref.artifact_id == artifact_id
        assert store.blob_path(ref).read_bytes() == data
        monkeypatch.setattr(store, "persist_bytes", lambda *a, **k: pytest.fail("repaired input"))
        assert import_remote_image(req, owner, store, recovery_only=True) == ref
        store.blob_path(ref).unlink()
        with pytest.raises(ValueError, match="no repair"):
            import_remote_image(req, owner, store, recovery_only=True)
    finally:
        owner.close()

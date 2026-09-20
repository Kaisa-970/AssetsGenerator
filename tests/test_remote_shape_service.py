import io
import sys

import pytest
from PIL import Image
from test_remote_http import request
from test_remote_shape_output import fixture

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.remote_protocol import RemoteRequest
from assets_generator.remote_service_store import RemoteServiceStore
from assets_generator.remote_service_worker import execute_service_job
from assets_generator.remote_shape_service import ShapeServiceHandler
from assets_generator.serialization import to_primitive
from assets_generator.worker import ProcessJobRequest


class Backend:
    def __init__(self, worker):
        self.worker = worker

    def generate(self, store, rgba, **parameters):
        self.worker.run(ProcessJobRequest([sys.executable, "-c", "pass"], store.root, 10, "shape"))
        return fixture(store)


def test_shape_handler_uses_registered_process_and_exports_validated_result(tmp_path):
    identity = request().identity
    source = LocalArtifactStore(tmp_path / "source")
    buffer = io.BytesIO()
    Image.new("RGBA", (2, 2), (255, 0, 0, 255)).save(buffer, format="PNG")
    ref = source.persist_bytes(
        buffer.getvalue(),
        kind="rgba_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGBA"},
    )
    manifest = source.get_manifest(ref.artifact_id)
    payload = {
        "operation": "shape_generation@1",
        "input_digest": "sha256:" + "a" * 64,
        "binding_digest": "sha256:" + "b" * 64,
        "parameters": {"seed": 42, "pipeline_type": "512"},
        "input_blobs": {
            "rgba": {"artifact_id": ref.artifact_id, "identity": to_primitive(manifest.identity)}
        },
    }
    req = RemoteRequest.create(identity, "shape", payload)
    service = RemoteServiceStore(tmp_path / "service.sqlite", identity)
    checks = []

    def verify():
        checks.append(True)
        return identity

    handler = ShapeServiceHandler(identity, tmp_path / "workspace", Backend, verify)
    try:
        service.put_blob(buffer.getvalue(), manifest.identity.blob_digest)
        service.submit(req)
        assert execute_service_job(service, req, handler).state == "succeeded"
        assert len(checks) == 2
        assert service.download(req, "mesh")[1].startswith(b"glTF")
        assert service.download(req, "shape_metadata")[0].media_type == "application/json"
        assert not list((tmp_path / "workspace").iterdir())
        bad = RemoteRequest.create(identity, "bad", {**payload, "model": "/another/model"})
        with pytest.raises(ValueError, match="invalid shape service request"):
            handler(bad, service)
    finally:
        service.close()

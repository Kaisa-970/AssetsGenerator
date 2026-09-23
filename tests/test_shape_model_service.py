import io
import json
import threading
import time

import trimesh
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.model_service_descriptor import detect_service
from assets_generator.models import BackendNativeFrame
from assets_generator.remote_http import RemoteJobClient
from assets_generator.remote_protocol import RemoteRequest
from assets_generator.remote_shape_output import import_shape_output
from assets_generator.serialization import to_primitive
from assets_generator.shape_model_service import ShapeModelService


def make_service(tmp_path, calls):
    def infer(path, parameters):
        with Image.open(path) as image:
            assert image.mode == "RGBA"
        calls.append(dict(parameters))
        return trimesh.creation.box(extents=[parameters["width"], 1, 1]).export(file_type="glb")

    return ShapeModelService(
        service_id="shape-example",
        display_name="示例",
        deployment={"model": "fixture-v1"},
        frame=BackendNativeFrame("native", "right", "+Y", None, "unknown", "relative_unit"),
        infer=infer,
        directory=tmp_path / "service",
        parameter_schema={
            "type": "object",
            "properties": {"width": {"type": "number", "minimum": 0.1}},
        },
        defaults={"width": 1.0},
    )


def prepare(service, tmp_path, key="job", parameters=None):
    source = LocalArtifactStore(tmp_path / "source")
    buffer = io.BytesIO()
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(buffer, format="PNG")
    ref = source.persist_bytes(
        buffer.getvalue(),
        kind="rgba_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGBA"},
    )
    identity = source.get_manifest(ref.artifact_id).identity
    service.store.put_blob(buffer.getvalue(), identity.blob_digest)
    return RemoteRequest.create(
        service.identity,
        key,
        {
            "operation": "shape_generation@1",
            "parameters": parameters or {"width": 2.0},
            "input_digest": "sha256:" + "a" * 64,
            "binding_digest": "sha256:" + "b" * 64,
            "input_blobs": {
                "rgba": {"artifact_id": ref.artifact_id, "identity": to_primitive(identity)}
            },
        },
    )


def wait_job(client, request):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.lookup(request)
        if job is not None and job.state in {"succeeded", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_http_discovery_automatic_dispatch_import_and_restart_idempotency(tmp_path):
    calls = []
    service = make_service(tmp_path, calls)
    request = prepare(service, tmp_path)
    service.start_worker()
    thread = threading.Thread(target=service.server.serve_forever, daemon=True)
    thread.start()
    try:
        assert detect_service(service.endpoint)["descriptor"]["defaults"] == {"width": 1.0}
        client = RemoteJobClient(service.endpoint)
        blob_identity = json.loads(request.payload_json)["input_blobs"]["rgba"]["identity"]
        client.upload_blob(
            service.identity,
            service.store.get_blob(blob_identity["blob_digest"]),
            blob_identity["blob_digest"],
        )
        client.submit(request)
        job = wait_job(client, request)
        assert job.state == "succeeded"
        assert client.submit(request).state == "succeeded"
        blobs = {
            name: client.download(request, job.job_id, name, expected_job=job)
            for name in ("mesh", "shape_metadata")
        }
        result = import_shape_output(LocalArtifactStore(tmp_path / "import"), blobs)
        assert result.backend_metadata["parameters"] == {"width": 2.0}
        assert calls == [{"width": 2.0}]
    finally:
        service.server.shutdown()
        thread.join()
        service.close()
    restored = make_service(tmp_path, calls)
    try:
        assert restored.store.lookup(request).state == "succeeded"
        restored.start_worker()
        assert restored.store.claim_next_queued() is None
        assert calls == [{"width": 2.0}]
    finally:
        restored.close()


def test_bad_input_terminal_failure_does_not_block_next_task(tmp_path):
    calls = []
    service = make_service(tmp_path, calls)
    bad = prepare(service, tmp_path, "bad", {"width": -1})
    good = prepare(service, tmp_path, "good")
    service.store.submit(bad)
    service.store.submit(good)
    service.start_worker()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and service.store.lookup(good).state != "succeeded":
            time.sleep(0.02)
        assert service.store.lookup(bad).state == "failed"
        assert service.store.lookup(good).state == "succeeded"
        assert calls == [{"width": 2.0}]
    finally:
        service.close()


def test_restart_unknown_running_blocks_inference(tmp_path):
    calls = []
    service = make_service(tmp_path, calls)
    first = prepare(service, tmp_path, "unknown")
    second = prepare(service, tmp_path, "queued")
    service.store.submit(first)
    assert service.store.claim_next_queued() == first
    service.store.submit(second)
    service.close()
    restored = make_service(tmp_path, calls)
    restored.start_worker()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and restored.last_worker_error is None:
            time.sleep(0.02)
        assert "unresolved running job" in restored.last_worker_error
        assert restored.store.lookup(second).state == "queued"
        assert calls == []
    finally:
        restored.close()

import io
import json

import pytest
from PIL import Image, ImageOps
from test_remote_http import request
from test_remote_service_http import serve

from assets_generator.remote_protocol import RemoteRequest
from assets_generator.remote_service_worker import ServiceOutput, execute_service_job
from assets_generator.serialization import sha256_bytes


def test_http_uploaded_image_worker_transform_and_restart(tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    data = buffer.getvalue()
    digest = sha256_bytes(data)
    req = RemoteRequest.create(request().identity, "transform", {"image_digest": digest})
    calls = []

    def transform(req, store):
        calls.append(req.submission_key)
        incoming = store.get_blob(json.loads(req.payload_json)["image_digest"])
        with Image.open(io.BytesIO(incoming)) as image:
            out = io.BytesIO()
            ImageOps.invert(image.convert("RGB")).save(out, format="PNG")
        return {"image": ServiceOutput(out.getvalue(), "image/png")}

    path = tmp_path / "service.sqlite"
    with serve(path) as (store, client, port):
        client.upload_blob(req.identity, data, digest)
        job = client.submit(req)
        assert execute_service_job(store, req, transform).state == "succeeded"
        with pytest.raises(ValueError, match="state conflict"):
            execute_service_job(store, req, transform)
        assert calls == ["transform"]
    with serve(path, port) as (_, client, _):
        output = client.download(req, job.job_id, "image")
        with Image.open(io.BytesIO(output)) as image:
            assert image.getpixel((0, 0)) == (0, 255, 255)


def test_worker_failure_and_interruption_do_not_replay(tmp_path):
    from assets_generator.remote_service_store import RemoteServiceStore

    req = request()
    path = tmp_path / "service.sqlite"
    store = RemoteServiceStore(path, req.identity)
    store.submit(req)

    def fail(*args):
        raise ValueError("invalid image")

    job = execute_service_job(store, req, fail)
    assert json.loads(job.error_json)["detail"] == "invalid image"
    req2 = RemoteRequest.create(req.identity, "interrupted", {})
    store.submit(req2)

    def interrupt(*args):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        execute_service_job(store, req2, interrupt)
    store.close()
    store = RemoteServiceStore(path, req.identity)
    try:
        assert store.lookup(req2).state == "running"
        with pytest.raises(ValueError, match="state conflict"):
            execute_service_job(store, req2, fail)
    finally:
        store.close()


def _interruptible_worker(path, ready):
    import time

    from assets_generator.remote_service_store import RemoteServiceStore

    req = request()
    store = RemoteServiceStore(path, req.identity)

    def wait_for_termination(*args):
        ready.send("claimed")
        while True:
            time.sleep(1)

    execute_service_job(store, req, wait_for_termination)


def test_killed_worker_keeps_durable_running_claim(tmp_path):
    import multiprocessing

    from assets_generator.remote_service_store import RemoteServiceStore

    req = request()
    path = tmp_path / "service.sqlite"
    store = RemoteServiceStore(path, req.identity)
    store.submit(req)
    store.close()
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_interruptible_worker, args=(path, writer))
    process.start()
    writer.close()
    try:
        assert reader.poll(15), "worker did not claim job"
        assert reader.recv() == "claimed"
        process.kill()
        process.join(10)
        assert process.exitcode is not None and process.exitcode < 0
        store = RemoteServiceStore(path, req.identity)
        try:
            assert store.lookup(req).state == "running"
            assert store.submit(req).state == "running"
            with pytest.raises(ValueError, match="state conflict"):
                execute_service_job(store, req, lambda *_: {})
        finally:
            store.close()
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        reader.close()

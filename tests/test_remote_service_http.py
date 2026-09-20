import threading
from contextlib import contextmanager

import pytest
from test_remote_http import request

from assets_generator.remote_http import RemoteJobClient, RemoteSubmissionConflict
from assets_generator.remote_service_http import create_remote_server
from assets_generator.remote_service_store import RemoteServiceStore
from assets_generator.serialization import sha256_bytes


@contextmanager
def serve(path, port=0, identity=None):
    store = RemoteServiceStore(path, identity or request().identity)
    server = create_remote_server(store, port=port)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield store, RemoteJobClient(f"http://127.0.0.1:{server.server_port}"), server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        store.close()


def test_http_restart_preserves_job_and_download(tmp_path):
    path = tmp_path / "service.sqlite"
    req = request()
    data = b"output fixture"
    digest = sha256_bytes(data)
    with serve(path) as (store, client, port):
        client.upload_blob(req.identity, data, digest)
        queued = client.submit(req)
        assert queued.state == "queued"
        store.transition(req, expected="queued", state="running")
    with serve(path, port) as (store, client, _):
        assert client.lookup(req).job_id == queued.job_id
        assert client.submit(req).state == "running"
        with pytest.raises(RemoteSubmissionConflict):
            client.submit(request({"different": True}))
        store.transition(
            req,
            expected="running",
            state="succeeded",
            result={
                "outputs": [
                    {
                        "output_id": "mesh",
                        "blob_digest": digest,
                        "byte_length": len(data),
                        "media_type": "model/gltf-binary",
                    }
                ]
            },
        )
    with serve(path, port) as (_, client, _):
        job = client.query(req, queued.job_id)
        assert job.state == "succeeded"
        assert client.download(req, job.job_id, "mesh", expected_job=job) == data

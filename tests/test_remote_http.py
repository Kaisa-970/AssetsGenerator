import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from assets_generator.remote_http import (
    RemoteJobClient,
    RemoteSubmissionConflict,
    RemoteTransportUnknown,
)
from assets_generator.remote_protocol import RemoteIdentity, RemoteRequest


@pytest.fixture
def server():
    state = {"jobs": {}, "submissions": 0, "drop": False, "mode": "ok"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, code, body):
            encoded = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_PUT(self):
            from assets_generator.serialization import sha256_bytes

            data = self.rfile.read(int(self.headers["Content-Length"]))
            digest = sha256_bytes(data)
            state.setdefault("uploads", []).append(data)
            if self.path != "/v1/blobs/" + digest.split(":")[1]:
                self.respond(400, {})
                return
            receipt = {
                "protocol_version": "1",
                "service_id": self.headers["X-Service-Id"],
                "backend_digest": self.headers["X-Backend-Digest"],
                "blob_digest": digest,
                "byte_length": len(data),
            }
            if state.get("bad_receipt"):
                receipt["blob_digest"] = "sha256:" + "0" * 64
            self.respond(201, receipt)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            key = request["submission_key"]
            if (
                key in state["jobs"]
                and state["jobs"][key]["request_digest"] != request["request_digest"]
            ):
                self.respond(409, {})
                return
            if key not in state["jobs"]:
                state["submissions"] += 1
                state["jobs"][key] = {
                    **{k: v for k, v in request.items() if k != "payload"},
                    "protocol_version": "1",
                    "job_id": "job-one",
                    "state": "running",
                    "result": None,
                    "error": None,
                }
            if state["drop"]:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.respond(202, state["jobs"][key])

        def do_GET(self):
            if "raw_response" in state:
                body = state["raw_response"]
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if state["mode"] == "timeout":
                state["release"].wait(2)
                return
            if state["mode"] == "redirect":
                self.send_response(302)
                self.send_header("Location", "/redirected")
                self.end_headers()
                return
            if state["mode"] == "oversize":
                self.respond(200, "x" * 2048)
                return
            if state["mode"] == "error":
                self.respond(503, {})
                return
            if self.path == "/v1/jobs/job-one/outputs/mesh":
                state["downloads"] = state.get("downloads", 0) + 1
                data = state.get("blob", b"test output")
                self.send_response(200)
                self.send_header("Content-Type", state.get("media", "model/gltf-binary"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            job = next(iter(state["jobs"].values()), None)
            if job is None:
                self.respond(404, {})
                return
            self.respond(200, job)

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            state,
            RemoteJobClient(f"http://127.0.0.1:{http.server_port}", max_response_bytes=1024),
        )
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


def request(payload=None):
    return RemoteRequest.create(
        RemoteIdentity("test", "sha256:" + "a" * 64), "same-key", payload or {"seed": 42}
    )


def test_lost_submission_response_lookup_finds_same_job(server):
    state, client = server
    assert client.lookup(request()) is None
    state["drop"] = True
    with pytest.raises(RemoteTransportUnknown):
        client.submit(request())
    assert state["submissions"] == 1
    job = client.lookup(request())
    assert job.job_id == "job-one" and job.state == "running"
    assert client.query(request(), job.job_id) == job
    assert state["submissions"] == 1
    state["drop"] = False
    assert client.submit(request()) == job
    assert state["submissions"] == 1
    with pytest.raises(RemoteSubmissionConflict):
        client.submit(request({"seed": 43}))
    assert state["submissions"] == 1


@pytest.mark.parametrize("mode", ["redirect", "oversize", "error"])
def test_nonprotocol_response_never_becomes_confirmed_failure(server, mode):
    state, client = server
    client.submit(request())
    state["mode"] = mode
    with pytest.raises(RemoteTransportUnknown):
        client.query(request(), "job-one")
    assert state["submissions"] == 1


def test_missing_pinned_job_and_identity_mismatch_are_unknown(server):
    state, client = server
    with pytest.raises(RemoteTransportUnknown):
        client.query(request(), "job-one")
    client.submit(request())
    state["jobs"]["same-key"]["backend_digest"] = "sha256:" + "b" * 64
    with pytest.raises(RemoteTransportUnknown, match="protocol"):
        client.lookup(request())


@pytest.mark.parametrize(
    "endpoint",
    [
        "file:///tmp/job",
        "https://user:secret@example.com",
        "https://example.com/path",
        "https://example.com?token=secret",
    ],
)
def test_endpoint_config_rejects_ambiguous_locations(endpoint):
    with pytest.raises(ValueError):
        RemoteJobClient(endpoint)


def test_read_timeout_preserves_uncertain_remote_state(server):
    state, client = server
    client.submit(request())
    state["mode"] = "timeout"
    state["release"] = threading.Event()
    client.timeout = 0.05
    try:
        with pytest.raises(RemoteTransportUnknown):
            client.query(request(), "job-one")
        assert state["submissions"] == 1
        assert state["jobs"]["same-key"]["state"] == "running"
    finally:
        state["release"].set()


def test_server_confirmed_failure_preserves_error_code(server):
    state, client = server
    client.submit(request())
    state["jobs"]["same-key"].update(
        state="failed", error={"code": "BACKEND_TIMEOUT", "detail": "service confirmed timeout"}
    )
    result = client.query(request(), "job-one")
    assert result.state == "failed"
    assert json.loads(result.error_json)["code"] == "BACKEND_TIMEOUT"


def output_job(state):
    from assets_generator.serialization import sha256_bytes

    state["jobs"]["same-key"].update(
        state="succeeded",
        result={
            "outputs": [
                {
                    "output_id": "mesh",
                    "blob_digest": sha256_bytes(b"test output"),
                    "byte_length": len(b"test output"),
                    "media_type": "model/gltf-binary",
                }
            ]
        },
    )


def test_download_checks_size_digest_and_media_before_return(server):
    state, client = server
    client.submit(request())
    output_job(state)
    assert client.download(request(), "job-one", "mesh") == b"test output"
    with pytest.raises(ValueError, match="limit"):
        client.download(request(), "job-one", "mesh", max_bytes=1)
    assert state["downloads"] == 1
    for bad in (b"bad", b"wrong bytes", b"test output extra"):
        state["blob"] = bad
        with pytest.raises(ValueError, match="size/digest"):
            client.download(request(), "job-one", "mesh")
    state["blob"] = b"test output"
    state["media"] = "text/html"
    with pytest.raises(ValueError, match="media"):
        client.download(request(), "job-one", "mesh")


@pytest.mark.parametrize("change", ["duplicate", "url", "traversal", "running"])
def test_invalid_output_descriptor_does_not_download(server, change):
    state, client = server
    client.submit(request())
    output_job(state)
    job = state["jobs"]["same-key"]
    output = job["result"]["outputs"][0]
    if change == "duplicate":
        job["result"]["outputs"].append(dict(output))
    elif change == "url":
        output["url"] = "https://other.invalid/model"
    elif change == "traversal":
        output["output_id"] = "../mesh"
    else:
        job.update(state="running", result=None)
    with pytest.raises(ValueError):
        client.download(request(), "job-one", "mesh")
    assert state.get("downloads", 0) == 0


def test_content_addressed_upload_checks_local_bytes_and_remote_receipt(server):
    from assets_generator.serialization import sha256_bytes

    state, client = server
    data = b"input pixels"
    identity = request().identity
    digest = sha256_bytes(data)
    with pytest.raises(ValueError, match="digest"):
        client.upload_blob(identity, data, "sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="limit"):
        client.upload_blob(identity, data, digest, max_bytes=1)
    assert state.get("uploads") is None
    client.upload_blob(identity, data, digest)
    client.upload_blob(identity, data, digest)
    assert state["uploads"] == [data, data]
    assert state["submissions"] == 0
    state["bad_receipt"] = True
    with pytest.raises(RemoteTransportUnknown, match="receipt"):
        client.upload_blob(identity, data, digest)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"job_id":"one","job_id":"two"}',
        b'{"value":NaN}',
        b'{"value":1e999}',
        b'{"nested":{"x":1,"x":2}}',
    ],
)
def test_ambiguous_wire_json_is_unknown(server, raw):
    state, client = server
    state["raw_response"] = raw
    with pytest.raises(RemoteTransportUnknown):
        client.lookup(request())
    assert state["submissions"] == 0

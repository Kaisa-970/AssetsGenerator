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

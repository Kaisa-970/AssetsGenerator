import io
import json
import socket
import threading
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from assets_generator.sam3d_http import (
    Sam3DClient,
    Sam3DConflict,
    Sam3DRejected,
    Sam3DUnknown,
    validate_options,
)


def png(mode, color):
    result = io.BytesIO()
    Image.new(mode, (3, 3), color).save(result, format="PNG")
    return result.getvalue()


@pytest.fixture
def upstream():
    state = {"posts": 0, "mode": "ok", "gets": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, raw, code=200):
            data = json.dumps(raw).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            assert self.headers["Authorization"] == "Bearer private-test-key"
            state["posts"] += 1
            body = self.rfile.read(int(self.headers["Content-Length"]))
            parsed = BytesParser(policy=policy.default).parsebytes(
                ("Content-Type: " + self.headers["Content-Type"] + "\r\n\r\n").encode() + body
            )
            state["fields"] = {
                part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                for part in parsed.iter_parts()
            }
            if state["mode"] == "drop":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if state["mode"] == "reject":
                self.send({"detail": "private-test-key"}, 401)
                return
            if state["mode"] == "conflict":
                self.send({"detail": "different request"}, 409)
                return
            if state["mode"] == "redirect":
                self.send_response(307)
                self.send_header("Location", "/unexpected")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            receipt = {"id": "job-1", "status": "queued", "status_url": "/api/v1/jobs/job-1"}
            for key in ("submission_key", "request_digest", "backend_digest"):
                if key in state["fields"]:
                    receipt[key] = state["fields"][key].decode()
            self.send(receipt, 202)

        def do_GET(self):
            state["gets"].append(self.path)
            if (
                self.path.startswith("/api/v1/jobs/by-key/")
                and state["mode"] == "ok"
                and "fields" in state
            ):
                receipt = {"id": "job-1", "status": "running", "status_url": "/api/v1/jobs/job-1"}
                receipt.update(
                    {
                        key: state["fields"][key].decode()
                        for key in ("submission_key", "request_digest", "backend_digest")
                    }
                )
                self.send(receipt)
            elif state["mode"] == "missing":
                self.send({}, 404)
            elif state["mode"] == "oversize_expired":
                self.send({"detail": "private-test-key" + "x" * 65536}, 410)
            elif state["mode"] == "malformed_expired":
                self.send({"detail": {"code": "WRONG", "receipt": "private-test-key"}}, 410)
            elif state["mode"] == "expired":
                self.send(
                    {
                        "detail": {
                            "code": "RESULT_EXPIRED_OR_MISSING",
                            "receipt": {
                                "id": "job-1",
                                "submission_key": "stable-key",
                                "request_digest": "sha256:" + "b" * 64,
                                "backend_digest": "sha256:" + "a" * 64,
                                "status_url": "/api/v1/jobs/job-1",
                            },
                        }
                    },
                    410,
                )
            elif state["mode"] == "foreign":
                self.send({"id": "other", "status": "running"})
            elif state["mode"] == "bad_files":
                self.send({"status": "completed", "files": {"model.glb": "http://other/file"}})
            elif self.path.endswith("/files/model.glb"):
                self.send({"placeholder": True})
            else:
                self.send(
                    {
                        "id": "job-1",
                        "status": "completed",
                        "files": {
                            name: f"/api/v1/jobs/job-1/files/{name}"
                            for name in ("model.glb", "parameters.json", "mask.png")
                        },
                    }
                )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Sam3DClient(f"http://127.0.0.1:{server.server_port}", "private-test-key"), state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_http_multipart_query_download(upstream):
    client, state = upstream
    image, mask = png("RGB", "red"), png("L", 255)
    assert client.submit_once(image, mask, {"seed": 8}) == "job-1"
    assert state["fields"]["image"] == image
    assert state["fields"]["mask"] == mask
    assert json.loads(state["fields"]["options"])["seed"] == 8
    assert client.query("job-1")["status"] == "completed"
    assert client.download("job-1", "model.glb")
    assert state["posts"] == 1


@pytest.mark.parametrize("mode", ["drop", "redirect"])
def test_uncertain_submission_never_retries_or_follows_redirect(upstream, mode):
    client, state = upstream
    state["mode"] = mode
    with pytest.raises(Sam3DUnknown) as error:
        client.submit_once(png("RGB", "red"), png("L", 255), {})
    assert "private-test-key" not in str(error.value)
    assert state["posts"] == 1
    assert not state["gets"]


def test_explicit_rejection_redacts_body(upstream):
    client, state = upstream
    state["mode"] = "reject"
    with pytest.raises(Sam3DRejected, match="401") as error:
        client.submit_once(png("RGB", "red"), png("L", 255), {})
    assert "private-test-key" not in str(error.value)


@pytest.mark.parametrize("mode", ["missing", "foreign", "bad_files"])
def test_query_errors_do_not_trigger_submission(upstream, mode):
    client, state = upstream
    state["mode"] = mode
    with pytest.raises(Sam3DUnknown):
        client.query("job-1")
    assert state["posts"] == 0


@pytest.mark.parametrize(
    "options",
    [
        {"seed": True},
        {"cfg1": float("nan")},
        {"texture": 7},
        {"bake": 1},
        {"reduction": 1},
        {"pipeline_type": "512"},
    ],
)
def test_options_reject_invalid_or_unrelated_parameters(options):
    with pytest.raises(ValueError):
        validate_options(options)


@pytest.mark.parametrize("mask", [png("L", 0), png("L", 128), png("RGB", "white")])
def test_invalid_inputs_never_submit(upstream, mask):
    client, state = upstream
    with pytest.raises(ValueError):
        client.submit_once(png("RGB", "red"), mask, {})
    assert state["posts"] == 0


def test_paths_cannot_escape_fixed_service(upstream):
    client, state = upstream
    for job in ("../other", "http://other", "abc?x=1"):
        with pytest.raises(ValueError):
            client.query(job)
    with pytest.raises(ValueError):
        client.download("job-1", "model.ply")
    assert not state["gets"]


def test_idempotent_submit_uses_normalized_digest(upstream):
    from assets_generator.serialization import canonical_json_bytes, sha256_bytes

    client, state = upstream
    image, mask = png("RGB", "red"), png("L", 255)
    backend = "sha256:" + "a" * 64
    assert (
        client.submit_once(image, mask, {}, submission_key="stable-key", backend_digest=backend)
        == "job-1"
    )
    opts = json.loads(state["fields"]["options"])
    assert type(opts["cfg1"]) is float
    expected = sha256_bytes(
        canonical_json_bytes(
            {
                "schema": "sam3d-request@1",
                "image": sha256_bytes(image),
                "mask": sha256_bytes(mask),
                "points": None,
                "options": opts,
            }
        )
    )
    assert state["fields"]["request_digest"].decode() == expected
    assert state["posts"] == 1


def test_lookup_distinguishes_not_found_and_expired(upstream):
    client, state = upstream
    request = "sha256:" + "b" * 64
    backend = "sha256:" + "a" * 64
    state["mode"] = "missing"
    assert (
        client.lookup("stable-key", request_digest=request, backend_digest=backend).state
        == "not_found"
    )
    state["mode"] = "expired"
    result = client.lookup("stable-key", request_digest=request, backend_digest=backend)
    assert result.state == "expired_or_missing"
    assert result.receipt is not None and result.receipt.job_id == "job-1"


def test_lookup_receipt_mismatch_blocks_recovery(upstream):
    client, state = upstream
    state["mode"] = "foreign"
    with pytest.raises(Sam3DUnknown, match="identity mismatch"):
        client.lookup(
            "stable-key", request_digest="sha256:" + "b" * 64, backend_digest="sha256:" + "a" * 64
        )


def test_submission_conflict_is_not_unknown(upstream):
    client, state = upstream
    state["mode"] = "conflict"
    with pytest.raises(Sam3DConflict, match="409"):
        client.submit_once(
            png("RGB", "red"),
            png("L", 255),
            {},
            submission_key="stable-key",
            backend_digest="sha256:" + "a" * 64,
        )


def test_lost_response_is_recovered_by_key_without_second_post(upstream):
    client, state = upstream
    state["mode"] = "drop"
    backend = "sha256:" + "a" * 64
    with pytest.raises(Sam3DUnknown):
        client.submit_once(
            png("RGB", "red"),
            png("L", 255),
            {},
            submission_key="stable-key",
            backend_digest=backend,
        )
    request = state["fields"]["request_digest"].decode()
    state["mode"] = "ok"
    result = client.lookup("stable-key", request_digest=request, backend_digest=backend)
    assert result.state == "registered" and result.receipt.job_id == "job-1"
    assert state["posts"] == 1


@pytest.mark.parametrize("changed", ["key", "request", "backend", "job"])
def test_expiration_receipt_must_match_original_binding(upstream, changed):
    client, state = upstream
    state["mode"] = "expired"
    with pytest.raises(Sam3DUnknown):
        client.lookup(
            "wrong" if changed == "key" else "stable-key",
            request_digest="sha256:" + ("c" if changed == "request" else "b") * 64,
            backend_digest="sha256:" + ("c" if changed == "backend" else "a") * 64,
            expected_job_id="different" if changed == "job" else None,
        )


def test_missing_previously_fixed_job_is_unknown(upstream):
    client, state = upstream
    state["mode"] = "missing"
    with pytest.raises(Sam3DUnknown):
        client.lookup(
            "stable-key",
            request_digest="sha256:" + "b" * 64,
            backend_digest="sha256:" + "a" * 64,
            expected_job_id="job-1",
        )


@pytest.mark.parametrize("mode", ["oversize_expired", "malformed_expired"])
def test_invalid_expiration_body_is_bounded_and_redacted(upstream, mode):
    client, state = upstream
    state["mode"] = mode
    with pytest.raises(Sam3DUnknown) as error:
        client.lookup(
            "stable-key", request_digest="sha256:" + "b" * 64, backend_digest="sha256:" + "a" * 64
        )
    assert "private-test-key" not in str(error.value)
    assert state["posts"] == 0


def test_optional_baking_options_preserve_legacy_identity():
    from assets_generator.sam3d_http import validate_options

    assert "bake_filter" not in validate_options({})
    updated = validate_options({"bake_filter": "mipmap", "bake_view_resolution": 512})
    assert updated["bake_filter"] == "mipmap"
    assert updated["bake_view_resolution"] == 512
    for bad in (
        {"bake_filter": "unknown"},
        {"bake_view_resolution": True},
        {"bake_view_resolution": 256},
    ):
        with pytest.raises(ValueError):
            validate_options(bad)

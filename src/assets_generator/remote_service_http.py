"""Loopback-only protocol server; dispatch is explicitly owned by a separate worker."""

from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .remote_protocol import RemoteRequest, decode_remote_json
from .remote_service_store import RemoteServiceStore
from .serialization import canonical_json_bytes


def create_remote_server(store: RemoteServiceStore, *, port: int = 0) -> ThreadingHTTPServer:
    """No public bind/authentication or implicit inference side effects in this slice."""

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _send(self, status: int, body: bytes, media: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _body(self, limit: int) -> bytes:
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
                raise ValueError("one content length required")
            length = int(lengths[0])
            if length < 1 or length > limit:
                raise ValueError("request size exceeds limit")
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("truncated request")
            return body

        def _job(self, request: RemoteRequest, status: int = 200) -> None:
            job = store.lookup(request)
            if job is None:
                self._send(404, b"{}")
                return
            wire = {k: v for k, v in request.to_dict().items() if k != "payload"}
            wire.update(
                protocol_version="1",
                job_id=job.job_id,
                state=job.state,
                result=json.loads(job.result_json) if job.result_json else None,
                error=json.loads(job.error_json) if job.error_json else None,
            )
            self._send(status, canonical_json_bytes(wire))

        def _handle(self, method: str) -> None:
            if self.headers.get("Origin"):
                self._send(403, b"{}")
                return
            try:
                if method == "PUT" and self.path.startswith("/v1/blobs/"):
                    if (
                        self.headers.get("X-Service-Id") != store.identity.service_id
                        or self.headers.get("X-Backend-Digest") != store.identity.backend_digest
                        or self.headers.get_content_type() != "application/octet-stream"
                    ):
                        raise ValueError("upload identity/media mismatch")
                    digest = "sha256:" + self.path.removeprefix("/v1/blobs/")
                    body = self._body(128 * 1024 * 1024)
                    store.put_blob(body, digest)
                    self._send(
                        201,
                        canonical_json_bytes(
                            {
                                "protocol_version": "1",
                                "service_id": store.identity.service_id,
                                "backend_digest": store.identity.backend_digest,
                                "blob_digest": digest,
                                "byte_length": len(body),
                            }
                        ),
                    )
                    return
                if method == "POST" and self.path == "/v1/jobs":
                    if self.headers.get_content_type() != "application/json":
                        raise ValueError("JSON required")
                    raw = decode_remote_json(self._body(1024 * 1024))
                    if not isinstance(raw, dict) or set(raw) != {
                        "service_id",
                        "backend_digest",
                        "submission_key",
                        "request_digest",
                        "payload",
                    }:
                        raise ValueError("invalid request envelope")
                    request = RemoteRequest.create(
                        store.identity, raw["submission_key"], raw["payload"]
                    )
                    if canonical_json_bytes(raw) != canonical_json_bytes(request.to_dict()):
                        raise ValueError("request identity mismatch")
                    previous = store.request_for(request.submission_key)
                    if previous is not None and previous != request:
                        self._send(409, b"{}")
                        return
                    store.submit(request)
                    self._job(request, 202)
                    return
                if method == "GET" and self.path.startswith("/v1/jobs/"):
                    suffix = self.path.removeprefix("/v1/jobs/")
                    if suffix.startswith("by-key/"):
                        suffix = suffix.removeprefix("by-key/")
                    parts = suffix.split("/")
                    found = store.request_for(parts[0])
                    if found is None:
                        self._send(404, b"{}")
                    elif len(parts) == 1:
                        self._job(found)
                    elif len(parts) == 3 and parts[1] == "outputs":
                        descriptor, body = store.download(found, parts[2])
                        self._send(200, body, descriptor.media_type)
                    else:
                        self._send(404, b"{}")
                    return
                self._send(404, b"{}")
            except (ValueError, KeyError, TypeError):
                self._send(400, b"{}")
            except (OSError, sqlite3.Error):
                self._send(503, b"{}")

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def do_PUT(self) -> None:
            self._handle("PUT")

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)

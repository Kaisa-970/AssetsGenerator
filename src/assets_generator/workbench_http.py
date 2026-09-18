"""Loopback transport for the fixed workbench; execution belongs to its service."""

from __future__ import annotations

import io
import json
import re
import secrets
import warnings
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any, Protocol, cast

from PIL import Image, UnidentifiedImageError

from .serialization import canonical_json_bytes

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
MAX_JSON_BYTES = 64 * 1024
_RUN = r"run_[A-Za-z0-9_-]{1,120}"
_KEY = r"[A-Za-z0-9_-]{1,128}"


@dataclass(frozen=True)
class OutputPayload:
    data: bytes
    media_type: str


class WorkbenchService(Protocol):
    def catalog(self) -> dict[str, Any]: ...
    def import_image(self, normalized_png: bytes) -> dict[str, Any]: ...
    def list_runs(self) -> list[dict[str, Any]]: ...
    def create_run(self, body: dict[str, Any]) -> dict[str, Any]: ...
    def get_run(self, run_id: str) -> dict[str, Any]: ...
    def command(self, run_id: str, action: str, body: dict[str, Any]) -> dict[str, Any]: ...
    def output(self, run_id: str, key: str) -> OutputPayload: ...


def normalize_upload(data: bytes) -> bytes:
    """Bound decoding before loading pixels; discard user filenames and metadata."""
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("image upload must be between 1 byte and 20 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise ValueError("image exceeds 24 million pixels")
                if getattr(source, "n_frames", 1) != 1:
                    raise ValueError("upload a single still image")
                if source.mode not in {"RGB", "L"}:
                    raise ValueError("upload an RGB or grayscale image without transparency")
                source.load()
                # Respect camera orientation before creating the immutable input.
                from PIL import ImageOps

                rgb = ImageOps.exif_transpose(source).convert("RGB")
                rgb.info.clear()
                target = io.BytesIO()
                rgb.save(target, format="PNG")
                return target.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("invalid or oversized image") from exc


def create_workbench_server(service: WorkbenchService, port: int = 8765) -> ThreadingHTTPServer:
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(15)

        def send_data(self, status: int, data: bytes, media_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, status: int, value: Any) -> None:
            self.send_data(status, canonical_json_bytes(value), "application/json")

        def allowed_origin(self) -> str | None:
            actual_port = cast(ThreadingHTTPServer, self.server).server_port
            host = self.headers.get("Host")
            if host not in {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}"}:
                return None
            return f"http://{host}"

        def do_GET(self) -> None:
            if self.allowed_origin() is None:
                self.send_json(403, {"error": "invalid Host"})
                return
            try:
                if self.path == "/":
                    self.send_data(
                        200,
                        files("assets_generator.resources")
                        .joinpath("node-workbench.html")
                        .read_bytes(),
                        "text/html; charset=utf-8",
                    )
                elif self.path == "/session":
                    self.send_json(200, {"token": token, "catalog": service.catalog()})
                elif self.path == "/runs":
                    self.send_json(200, {"runs": service.list_runs()})
                elif match := re.fullmatch(rf"/runs/({_RUN})", self.path):
                    self.send_json(200, service.get_run(match[1]))
                elif match := re.fullmatch(rf"/runs/({_RUN})/outputs/({_KEY})", self.path):
                    output = service.output(match[1], match[2])
                    self.send_data(200, output.data, output.media_type)
                else:
                    self.send_json(404, {"error": "unknown route"})
            except (KeyError, FileNotFoundError):
                self.send_json(404, {"error": "unknown run or output"})
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            except Exception:
                self.send_json(500, {"error": "cannot read workbench state"})

        def read_body(self, maximum: int) -> bytes:
            if self.headers.get("Transfer-Encoding") is not None:
                raise ValueError("chunked requests are not supported")
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1:
                raise ValueError("one Content-Length header is required")
            length = int(lengths[0])
            if not 0 < length <= maximum:
                raise ValueError(f"request body must be between 1 and {maximum} bytes")
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("incomplete request body")
            return data

        def do_POST(self) -> None:
            origin = self.allowed_origin()
            if (
                origin is None
                or self.headers.get("Origin") != origin
                or not secrets.compare_digest(self.headers.get("X-Workbench-Token", ""), token)
            ):
                self.send_json(403, {"error": "invalid workbench session"})
                return
            try:
                if self.path == "/inputs":
                    image = normalize_upload(self.read_body(MAX_UPLOAD_BYTES))
                    self.send_json(201, service.import_image(image))
                    return
                match = re.fullmatch(rf"/runs/({_RUN})/(mask-preview|decision|retry)", self.path)
                if self.path != "/runs" and match is None:
                    self.send_json(404, {"error": "unknown route"})
                    return
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("JSON Content-Type is required")
                body = json.loads(self.read_body(MAX_JSON_BYTES))
                if not isinstance(body, dict):
                    raise ValueError("request must be a JSON object")
                if match is not None:
                    result = service.command(match[1], match[2], body)
                else:
                    result = service.create_run(body)
                self.send_json(202, result)
            except (FileExistsError, RuntimeError) as exc:
                self.send_json(409, {"error": str(exc)})
            except (ValueError, TypeError, KeyError) as exc:
                self.send_json(400, {"error": str(exc)})
            except Exception:
                self.send_json(500, {"error": "command failed; reload the persisted run state"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)

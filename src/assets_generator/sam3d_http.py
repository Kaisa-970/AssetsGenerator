"""SAM3D REST transport only; not a durable executor or a registered DAG adapter.

The caller must durably authorize submission before calling submit_once. A lost
acknowledgement can be recovered by key with REST 1.1. Recovery validates the
original request and deployment identities; it never automatically resubmits.
"""

from __future__ import annotations

import io
import math
import re
import uuid
from dataclasses import dataclass
from http.client import HTTPException
from typing import Any, Literal
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image

from .errors import ServiceExecutionUncertain
from .remote_http import RemoteJobClient
from .remote_protocol import decode_remote_json
from .serialization import canonical_json_bytes, sha256_bytes


class Sam3DUnknown(ServiceExecutionUncertain):
    """Keep the original claim: remote execution may exist. Never auto-resubmit."""


class Sam3DRejected(ValueError):
    """An explicit HTTP rejection before an upstream job was acknowledged."""


class Sam3DConflict(Sam3DRejected):
    """The service refused this request/key/deployment combination (HTTP 409)."""


@dataclass(frozen=True)
class Sam3DReceipt:
    job_id: str
    submission_key: str
    request_digest: str
    backend_digest: str

    @classmethod
    def parse(cls, raw: object, expected: tuple[str, str, str]) -> Sam3DReceipt:
        if not isinstance(raw, dict):
            raise Sam3DUnknown("missing SAM3D receipt")
        try:
            job = _job_id(raw.get("id"))
        except ValueError:
            raise Sam3DUnknown("invalid SAM3D receipt job ID") from None
        key, request_digest, backend_digest = expected
        if (
            raw.get("submission_key") != key
            or raw.get("request_digest") != request_digest
            or raw.get("backend_digest") != backend_digest
            or raw.get("status_url") != f"/api/v1/jobs/{job}"
        ):
            raise Sam3DUnknown("SAM3D receipt identity mismatch")
        return cls(job, key, request_digest, backend_digest)


class Sam3DExpired(RuntimeError):
    """A registered request's result is expired OR missing; never regenerate it."""

    def __init__(self, receipt: Sam3DReceipt):
        super().__init__("SAM3D registered result expired or missing")
        self.receipt = receipt


@dataclass(frozen=True)
class Sam3DLookup:
    state: Literal["not_found", "registered", "expired_or_missing"]
    receipt: Sam3DReceipt | None = None
    job_status: str | None = None


class _NotFound(RuntimeError):
    pass


def _expected(key: str, request_digest: str, backend_digest: str) -> tuple[str, str, str]:
    _job_id(key)
    for value in (request_digest, backend_digest):
        if not isinstance(value, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise ValueError("SAM3D recovery requires request and backend digests")
    return key, request_digest, backend_digest


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def _job_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("invalid SAM3D job ID")
    return value


def validate_options(options: dict[str, Any]) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "seed": 42,
        "steps1": 25,
        "steps2": 25,
        "cfg1": 7,
        "cfg2": 1,
        "bake": False,
        "texture": 1024,
        "reduction": 0,
    }
    if not isinstance(options, dict) or set(options) - set(defaults):
        raise ValueError("unknown SAM3D options")
    result = {**defaults, **options}
    for key, lo, hi in (("seed", 0, 2**32 - 1), ("steps1", 1, 100), ("steps2", 1, 100)):
        if type(result[key]) is not int or not lo <= result[key] <= hi:
            raise ValueError(f"invalid SAM3D {key}")
    for key, upper in (("cfg1", 15), ("cfg2", 15), ("reduction", 0.95)):
        value = result[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= upper:
            raise ValueError(f"invalid SAM3D {key}")
    if type(result["bake"]) is not bool:
        raise ValueError("invalid SAM3D bake")
    if type(result["texture"]) is not int or result["texture"] not in (512, 1024, 2048):
        raise ValueError("invalid SAM3D texture")
    for key in ("cfg1", "cfg2", "reduction"):
        result[key] = float(result[key])
    return result


def validate_images(image: bytes, mask: bytes) -> None:
    """Require explicit, orientation-free RGB/PNG mask inputs; never silently rotate."""
    for data in (image, mask):
        if not isinstance(data, bytes) or not 0 < len(data) <= 30 * 1024 * 1024:
            raise ValueError("SAM3D input exceeds file limit or is empty")
    with Image.open(io.BytesIO(image)) as rgb, Image.open(io.BytesIO(mask)) as alpha:
        for frame in (rgb, alpha):
            if frame.width * frame.height > 25_000_000 or getattr(frame, "n_frames", 1) != 1:
                raise ValueError("SAM3D input requires one bounded frame")
            if frame.getexif().get(274, 1) != 1 or "transparency" in frame.info:
                raise ValueError("normalize orientation/transparency in an explicit input node")
        if rgb.format not in {"PNG", "JPEG"} or rgb.mode != "RGB":
            raise ValueError("SAM3D requires RGB PNG/JPEG")
        if alpha.format != "PNG" or alpha.mode not in {"1", "L"} or rgb.size != alpha.size:
            raise ValueError("SAM3D requires a same-size grayscale PNG mask")
        rgb.load()
        values = set(alpha.convert("L").tobytes())
        if not values <= {0, 255} or 255 not in values:
            raise ValueError("SAM3D mask requires binary values and foreground")


class Sam3DClient:
    """No retries, redirect following, cancellation, automatic polling or credential logging."""

    def __init__(self, endpoint: str, api_key: str, *, timeout: float = 30):
        self.endpoint = RemoteJobClient(endpoint, timeout=timeout).endpoint
        if not isinstance(api_key, str) or not api_key or any(c.isspace() for c in api_key):
            raise ValueError("SAM3D credential must be nonempty and contain no whitespace")
        self._api_key = api_key
        self.timeout = timeout
        self.opener = build_opener(_NoRedirect())

    def _exchange(
        self,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        limit: int = 1024 * 1024,
        submitting: bool = False,
        expected: tuple[str, str, str] | None = None,
        lookup: bool = False,
    ) -> bytes:
        headers = {"Authorization": "Bearer " + self._api_key}
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(self.endpoint + path, data=body, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status != (202 if submitting else 200):
                    raise Sam3DUnknown("unexpected SAM3D response status")
                data = bytes(response.read(limit + 1))
                if len(data) > limit:
                    raise Sam3DUnknown("SAM3D response exceeds limit")
                return data
        except HTTPError as error:
            code = error.code
            try:
                # Only 410 needs a response body. Bound and sanitize it before exposing evidence.
                if code == 410 and expected is not None:
                    try:
                        data = error.read(64 * 1024 + 1)
                        if len(data) > 64 * 1024:
                            raise ValueError("oversize")
                        raw = decode_remote_json(data)
                        detail = raw.get("detail") if isinstance(raw, dict) else None
                        if (
                            not isinstance(detail, dict)
                            or detail.get("code") != "RESULT_EXPIRED_OR_MISSING"
                        ):
                            raise ValueError("invalid expiration")
                        receipt = Sam3DReceipt.parse(detail.get("receipt"), expected)
                    except (OSError, HTTPException, ValueError):
                        raise Sam3DUnknown("invalid SAM3D expiration response") from None
                    raise Sam3DExpired(receipt) from None
                if code == 404 and lookup:
                    raise _NotFound() from None
                if code == 409 and submitting:
                    raise Sam3DConflict("SAM3D request or deployment conflict: HTTP 409") from None
                if submitting and code in {401, 413, 422, 429}:
                    raise Sam3DRejected(f"SAM3D submission rejected: HTTP {code}") from None
                raise Sam3DUnknown(f"SAM3D outcome unknown: HTTP {code}") from None
            finally:
                error.close()
        except (OSError, HTTPException, ValueError) as error:
            if isinstance(error, (Sam3DRejected, Sam3DConflict)):
                raise
            raise Sam3DUnknown("SAM3D transport outcome unknown; do not resubmit") from None

    def _json(self, path: str, **kwargs: Any) -> dict[str, Any]:
        data = self._exchange(path, **kwargs)
        try:
            raw = decode_remote_json(data)
            if not isinstance(raw, dict):
                raise ValueError("object required")
            return raw
        except ValueError:
            raise Sam3DUnknown("invalid SAM3D JSON response") from None

    def health(self) -> dict[str, Any]:
        return self._json("/api/v1/health")

    def capabilities(self) -> dict[str, Any]:
        return self._json("/api/v1/capabilities")

    def lookup(
        self,
        submission_key: str,
        *,
        request_digest: str,
        backend_digest: str,
        expected_job_id: str | None = None,
    ) -> Sam3DLookup:
        state: Literal["registered", "expired_or_missing"]
        expected = _expected(submission_key, request_digest, backend_digest)
        if expected_job_id is not None:
            _job_id(expected_job_id)
        try:
            raw = self._json(
                "/api/v1/jobs/by-key/" + submission_key,
                expected=expected,
                lookup=True,
            )
        except _NotFound:
            if expected_job_id is not None:
                raise Sam3DUnknown("previously recorded SAM3D job key is missing") from None
            return Sam3DLookup("not_found")
        except Sam3DExpired as error:
            receipt = error.receipt
            state = "expired_or_missing"
            status = None
        else:
            receipt = Sam3DReceipt.parse(raw, expected)
            state = "registered"
            status = raw.get("status")
            if status not in {"queued", "waiting_gpu", "running", "completed", "failed"}:
                raise Sam3DUnknown("invalid SAM3D lookup status")
        if expected_job_id is not None and receipt.job_id != expected_job_id:
            raise Sam3DUnknown("SAM3D lookup changed the recorded job ID")
        return Sam3DLookup(state, receipt, status)

    def submit_once(
        self,
        image: bytes,
        mask: bytes,
        options: dict[str, Any],
        *,
        submission_key: str | None = None,
        backend_digest: str | None = None,
    ) -> str:
        """Explicit one-shot POST. Production callers must persist intent before entry."""
        validate_images(image, mask)
        normalized = validate_options(options)
        boundary = "asset-" + uuid.uuid4().hex
        extras: list[tuple[str, str | None, str, bytes]] = []
        expected = None
        if submission_key is not None:
            _job_id(submission_key)
            if not isinstance(backend_digest, str) or not re.fullmatch(
                r"sha256:[a-f0-9]{64}", backend_digest
            ):
                raise ValueError("idempotent SAM3D submission requires backend_digest")
            request_digest = sha256_bytes(
                canonical_json_bytes(
                    {
                        "schema": "sam3d-request@1",
                        "image": sha256_bytes(image),
                        "mask": sha256_bytes(mask),
                        "points": None,
                        "options": normalized,
                    }
                )
            )
            expected = _expected(submission_key, request_digest, backend_digest)
            extras = [
                (name, None, "text/plain", value.encode())
                for name, value in (
                    ("submission_key", submission_key),
                    ("request_digest", request_digest),
                    ("backend_digest", backend_digest),
                )
            ]
        elif backend_digest is not None:
            raise ValueError("backend_digest requires submission_key")
        chunks = []
        for name, filename, media, data in (
            ("image", "image", "application/octet-stream", image),
            ("mask", "mask.png", "image/png", mask),
            ("options", None, "application/json", canonical_json_bytes(normalized)),
            *extras,
        ):
            disposition = f'Content-Disposition: form-data; name="{name}"'
            if filename:
                disposition += f'; filename="{filename}"'
            chunks.append(
                f"--{boundary}\r\n{disposition}\r\nContent-Type: {media}\r\n\r\n".encode()
                + data
                + b"\r\n"
            )
        body = b"".join(chunks) + f"--{boundary}--\r\n".encode()
        raw = self._json(
            "/api/v1/jobs",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
            submitting=True,
            expected=expected,
        )
        try:
            job = _job_id(raw.get("id"))
            if (
                raw.get("status") not in {"queued", "waiting_gpu", "running", "completed", "failed"}
                or raw.get("status_url") != f"/api/v1/jobs/{job}"
            ):
                raise ValueError("submission receipt mismatch")
            if submission_key is not None and (
                raw.get("submission_key") != submission_key
                or raw.get("request_digest") != request_digest
                or raw.get("backend_digest") != backend_digest
            ):
                raise ValueError("SAM3D idempotency receipt mismatch")
            return job
        except ValueError:
            raise Sam3DUnknown(
                "SAM3D submission acknowledgement invalid; do not resubmit"
            ) from None

    def query(self, job_id: str) -> dict[str, Any]:
        job = _job_id(job_id)
        raw = self._json(f"/api/v1/jobs/{job}")
        if raw.get("id", job) != job or raw.get("status") not in {
            "queued",
            "waiting_gpu",
            "running",
            "completed",
            "failed",
        }:
            raise Sam3DUnknown("invalid SAM3D job status")
        if raw["status"] == "completed":
            files = raw.get("files")
            if not isinstance(files, dict) or not {
                "model.glb",
                "parameters.json",
                "mask.png",
            } <= set(files):
                raise Sam3DUnknown("SAM3D completed job lacks required files")
            for name, url in files.items():
                if (
                    name
                    not in {
                        "model.glb",
                        "model.ply",
                        "parameters.json",
                        "mask.png",
                        "evidence.json",
                    }
                    or url != f"/api/v1/jobs/{job}/files/{name}"
                ):
                    raise Sam3DUnknown("unexpected SAM3D output path")
        return raw

    def download(self, job_id: str, name: str) -> bytes:
        job = _job_id(job_id)
        limits = {
            "model.glb": 128 * 1024 * 1024,
            "parameters.json": 1024 * 1024,
            "evidence.json": 4 * 1024 * 1024,
            "mask.png": 30 * 1024 * 1024,
        }
        if name not in limits:
            raise ValueError("unsupported SAM3D output; Gaussian PLY is not a mesh")
        return self._exchange(f"/api/v1/jobs/{job}/files/{name}", limit=limits[name])

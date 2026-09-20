"""Bounded remote-job transport. No implicit retry, redirect, cancellation or dispatch."""

from __future__ import annotations

import json
import math
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .remote_protocol import RemoteJob, RemoteOutput, RemoteRequest, _identifier
from .serialization import canonical_json_bytes, sha256_bytes


class RemoteTransportUnknown(RuntimeError):
    """The remote computation's outcome is unknown; never interpret as failed."""


class RemoteSubmissionConflict(ValueError):
    """The service refused reuse of the submission key."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


class RemoteJobClient:
    def __init__(
        self, endpoint: str, *, timeout: float = 10.0, max_response_bytes: int = 1024 * 1024
    ):
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("remote endpoint must be an HTTP(S) origin without credentials")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("response limit must be positive")
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.opener = build_opener(_NoRedirect())

    def submit(self, request: RemoteRequest) -> RemoteJob:
        raw = self._exchange("/v1/jobs", request.to_dict())
        return self._parse(raw, request)

    def lookup(
        self, request: RemoteRequest, *, expected_job_id: str | None = None
    ) -> RemoteJob | None:
        raw = self._exchange("/v1/jobs/by-key/" + request.submission_key, allow_missing=True)
        return None if raw is None else self._parse(raw, request, expected_job_id)

    def query(self, request: RemoteRequest, job_id: str) -> RemoteJob:
        _identifier(job_id, "job_id")
        raw = self._exchange("/v1/jobs/" + job_id)
        return self._parse(raw, request, job_id)

    def download(
        self,
        request: RemoteRequest,
        job_id: str,
        output_id: str,
        *,
        max_bytes: int = 128 * 1024 * 1024,
        expected_job: RemoteJob | None = None,
    ) -> bytes:
        """Validate current job identity and return digest-checked bytes, not an Artifact."""
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("download limit must be positive")
        job = self.query(request, job_id)
        if expected_job is not None and job != expected_job:
            raise ValueError("remote result differs from pinned job evidence")
        descriptor = RemoteOutput.from_job(job, output_id)
        if descriptor.byte_length > max_bytes:
            raise ValueError("remote output exceeds configured download limit")
        path = f"/v1/jobs/{job.job_id}/outputs/{descriptor.output_id}"
        try:
            with self.opener.open(
                Request(self.endpoint + path, headers={"Accept": descriptor.media_type}),
                timeout=self.timeout,
            ) as response:
                if (
                    response.status != 200
                    or response.headers.get_content_type() != descriptor.media_type
                ):
                    raise ValueError("remote output status/media mismatch")
                data = bytes(response.read(descriptor.byte_length + 1))
        except HTTPError as error:
            error.close()
            raise RemoteTransportUnknown(f"remote output HTTP {error.code}") from error
        except (OSError, HTTPException) as error:
            raise RemoteTransportUnknown("remote output download interrupted") from error
        if len(data) != descriptor.byte_length or sha256_bytes(data) != descriptor.blob_digest:
            raise ValueError("remote output size/digest mismatch")
        return data

    @staticmethod
    def _parse(raw: object, request: RemoteRequest, job_id: str | None = None) -> RemoteJob:
        try:
            return RemoteJob.parse(raw, request, expected_job_id=job_id)
        except (ValueError, TypeError, KeyError) as error:
            raise RemoteTransportUnknown("remote response failed protocol validation") from error

    def _exchange(
        self, path: str, payload: dict[str, Any] | None = None, *, allow_missing: bool = False
    ) -> object:
        data = None if payload is None else canonical_json_bytes(payload)
        request = Request(
            self.endpoint + path,
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="GET" if data is None else "POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status not in ({200, 202} if data is not None else {200}):
                    raise RemoteTransportUnknown("unexpected remote HTTP status")
                if response.headers.get_content_type() != "application/json":
                    raise RemoteTransportUnknown("remote response is not JSON")
                body = response.read(self.max_response_bytes + 1)
                if len(body) > self.max_response_bytes:
                    raise RemoteTransportUnknown("remote response exceeds limit")
                return json.loads(body)
        except HTTPError as error:
            error.close()
            if error.code == 404 and allow_missing:
                return None
            if error.code == 409 and data is not None:
                raise RemoteSubmissionConflict("remote submission key conflict") from error
            raise RemoteTransportUnknown(
                f"remote HTTP {error.code}; job outcome unknown"
            ) from error
        except (OSError, HTTPException, ValueError) as error:
            raise RemoteTransportUnknown(
                "remote transport/response unavailable; job outcome unknown"
            ) from error

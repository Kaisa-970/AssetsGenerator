"""Bounded ComfyUI transport with durable one-shot POST and read-only history.

History is untrusted observation, not a verified execution result. Output imports
and workflow correlation belong to the composite adapter, still to be implemented.
"""

from __future__ import annotations

import uuid
from http.client import HTTPException
from typing import Any
from urllib.request import Request

from .comfy_submission import ComfySubmissionJournal, ComfySubmissionUnknown
from .remote_http import RemoteJobClient
from .remote_protocol import decode_remote_json
from .serialization import canonical_json_bytes


class ComfyClient:
    def __init__(
        self, endpoint: str, *, timeout: float = 10.0, max_response_bytes: int = 1024 * 1024
    ):
        # Share origin validation and bounded, nonredirecting urllib configuration.
        self.transport = RemoteJobClient(
            endpoint, timeout=timeout, max_response_bytes=max_response_bytes
        )
        self.endpoint = self.transport.endpoint

    def _json(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        request = Request(
            self.endpoint + path,
            data=canonical_json_bytes(body) if body is not None else None,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST" if body is not None else "GET",
        )
        try:
            with self.transport.opener.open(request, timeout=self.transport.timeout) as response:
                if (
                    response.status != 200
                    or response.headers.get_content_type() != "application/json"
                ):
                    raise ValueError("invalid ComfyUI response status or content type")
                data = response.read(self.transport.max_response_bytes + 1)
                if len(data) > self.transport.max_response_bytes:
                    raise ValueError("ComfyUI response exceeds limit")
                result = decode_remote_json(data)
                if not isinstance(result, dict):
                    raise ValueError("ComfyUI response must be an object")
                return result
        except (OSError, HTTPException, ValueError) as error:
            raise ComfySubmissionUnknown("ComfyUI transport uncertain; do not resubmit") from error

    def submit(self, journal: ComfySubmissionJournal, key: str) -> dict[str, Any]:
        record = journal.read(key)
        if record["deployment"].get("endpoint") != self.endpoint:
            raise ValueError("ComfyUI journal endpoint differs from configured service")
        return journal.submit_once(key, lambda body: self._json("/prompt", body))

    def history(self, journal: ComfySubmissionJournal, key: str) -> dict[str, Any]:
        record = journal.read(key)
        if record["deployment"].get("endpoint") != self.endpoint:
            raise ValueError("ComfyUI journal endpoint differs from configured service")
        prompt_id = record["prompt_id"]
        if str(uuid.UUID(prompt_id)) != prompt_id:
            raise ValueError("invalid ComfyUI prompt UUID")
        return self._json("/history/" + prompt_id)

    def observe(self, journal: ComfySubmissionJournal, key: str) -> dict[str, Any]:
        """Correlate history only; never resubmit or declare outputs imported."""
        from .comfy_history import validate_history

        return validate_history(journal.read(key), self.history(journal, key))

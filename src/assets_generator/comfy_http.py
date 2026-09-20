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
        record = journal.read(key)
        if record["deployment"].get("endpoint") != self.endpoint:
            raise ValueError("ComfyUI journal endpoint differs from configured service")
        fixed = journal.observation(key)
        if fixed is not None:
            return fixed
        return journal.record_history(key, self.history(journal, key))

    def download_image(
        self,
        journal: ComfySubmissionJournal,
        key: str,
        *,
        node: str,
        index: int,
        mode: str,
        max_bytes: int = 32 * 1024 * 1024,
    ) -> bytes:
        """Read a fixed output descriptor; bytes are not yet durable imported evidence."""
        from .comfy_output import image_query, validate_png

        if type(max_bytes) is not int or not 0 < max_bytes <= 128 * 1024 * 1024:
            raise ValueError("invalid ComfyUI image download limit")
        if mode not in {"RGB", "RGBA"}:
            raise ValueError("invalid ComfyUI image mode")
        record = journal.read(key)
        if record["deployment"].get("endpoint") != self.endpoint:
            raise ValueError("ComfyUI journal endpoint differs from configured service")
        observation = journal.observation(key)
        if observation is None:
            raise ValueError("ComfyUI output observation must be fixed before download")
        path = image_query(observation, node, index)
        try:
            with self.transport.opener.open(
                Request(self.endpoint + path, headers={"Accept": "image/png"}),
                timeout=self.transport.timeout,
            ) as response:
                if response.status != 200 or response.headers.get_content_type() != "image/png":
                    raise ValueError("unexpected ComfyUI image response")
                data: bytes = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError("ComfyUI image exceeds download limit")
                validate_png(data, mode=mode)
                return data
        except (OSError, HTTPException, ValueError) as error:
            raise ComfySubmissionUnknown("ComfyUI output unavailable or invalid") from error

"""Immutable ownership of a remote request by one DAG execution attempt."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from .compiled_plan import digest
from .remote_protocol import RemoteIdentity, RemoteRequest, _digest, _identifier


@dataclass(frozen=True)
class RemoteAttemptBinding:
    run_id: str
    node_id: str
    attempt: int
    input_digest: str
    binding_digest: str
    endpoint: str
    service_id: str
    backend_digest: str
    payload_json: str
    submission_key: str
    request_digest: str

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        node_id: str,
        attempt: int,
        input_digest: str,
        binding_digest: str,
        endpoint: str,
        request: RemoteRequest,
    ) -> RemoteAttemptBinding:
        key = cls.key_for(run_id, node_id, attempt)
        if request.submission_key != key:
            raise ValueError("remote submission key must identify its DAG attempt")
        return cls(
            run_id,
            node_id,
            attempt,
            input_digest,
            binding_digest,
            endpoint,
            request.identity.service_id,
            request.identity.backend_digest,
            request.payload_json.decode("utf-8"),
            key,
            request.request_digest,
        )

    @staticmethod
    def key_for(run_id: str, node_id: str, attempt: int) -> str:
        return (
            "dag_"
            + digest({"run_id": run_id, "node_id": node_id, "attempt": attempt}).split(":")[1]
        )

    def request(self) -> RemoteRequest:
        return RemoteRequest(
            RemoteIdentity(self.service_id, self.backend_digest),
            self.submission_key,
            self.payload_json.encode("utf-8"),
        )

    def __post_init__(self) -> None:
        _identifier(self.run_id, "run_id")
        if not isinstance(self.node_id, str) or not self.node_id:
            raise ValueError("remote node_id required")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("remote attempt must be positive")
        _digest(self.input_digest, "input_digest")
        _digest(self.binding_digest, "binding_digest")
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("remote binding endpoint must be a normalized origin")
        if self.submission_key != self.key_for(self.run_id, self.node_id, self.attempt):
            raise ValueError("remote submission key does not match owner")
        request = self.request()
        if request.request_digest != self.request_digest:
            raise ValueError("remote request digest mismatch")
        payload = json.loads(self.payload_json)
        if (
            payload.get("input_digest") != self.input_digest
            or payload.get("binding_digest") != self.binding_digest
        ):
            raise ValueError("remote payload must bind actual input and backend binding digests")

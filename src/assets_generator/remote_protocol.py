"""Strict wire identities for remote jobs; transport success is not asset validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .compiled_plan import digest
from .serialization import canonical_json_bytes


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError(f"invalid {label}")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        raise ValueError(f"invalid {label}")
    return value


@dataclass(frozen=True)
class RemoteIdentity:
    service_id: str
    backend_digest: str

    def __post_init__(self) -> None:
        _identifier(self.service_id, "service_id")
        _digest(self.backend_digest, "backend_digest")


@dataclass(frozen=True)
class RemoteRequest:
    identity: RemoteIdentity
    submission_key: str
    # Store canonical bytes, never retain caller-owned mutable dictionaries.
    payload_json: bytes

    def __post_init__(self) -> None:
        import json

        _identifier(self.submission_key, "submission_key")
        raw = json.loads(self.payload_json)
        if not isinstance(raw, dict) or canonical_json_bytes(raw) != self.payload_json:
            raise ValueError("payload must be a canonical JSON object")

    @classmethod
    def create(cls, identity: RemoteIdentity, key: str, payload: dict[str, Any]) -> RemoteRequest:
        return cls(identity, key, canonical_json_bytes(payload))

    @property
    def request_digest(self) -> str:
        import json

        return digest(
            {
                "service_id": self.identity.service_id,
                "backend_digest": self.identity.backend_digest,
                "payload": json.loads(self.payload_json),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        import json

        return {
            "service_id": self.identity.service_id,
            "backend_digest": self.identity.backend_digest,
            "submission_key": self.submission_key,
            "request_digest": self.request_digest,
            "payload": json.loads(self.payload_json),
        }


@dataclass(frozen=True)
class RemoteJob:
    job_id: str
    state: str
    result_json: bytes | None
    error_json: bytes | None

    @classmethod
    def parse(
        cls, raw: object, request: RemoteRequest, *, expected_job_id: str | None = None
    ) -> RemoteJob:
        fields = {
            "protocol_version",
            "service_id",
            "backend_digest",
            "submission_key",
            "request_digest",
            "job_id",
            "state",
            "result",
            "error",
        }
        if not isinstance(raw, dict) or set(raw) != fields or raw["protocol_version"] != "1":
            raise ValueError("invalid remote job envelope")
        for key, expected in request.to_dict().items():
            if key != "payload" and raw[key] != expected:
                raise ValueError(f"remote job identity mismatch: {key}")
        job_id = _identifier(raw["job_id"], "job_id")
        if expected_job_id is not None and job_id != expected_job_id:
            raise ValueError("remote job ID changed")
        state, result, error = raw["state"], raw["result"], raw["error"]
        if state not in ("queued", "running", "succeeded", "failed"):
            raise ValueError("unknown remote job state")
        if state == "succeeded":
            if not isinstance(result, dict) or error is not None:
                raise ValueError("success requires result and no error")
        elif state == "failed":
            if (
                result is not None
                or not isinstance(error, dict)
                or set(error) != {"code", "detail"}
            ):
                raise ValueError("failure requires explicit error")
            if any(not isinstance(error[key], str) or not error[key].strip() for key in error):
                raise ValueError("failure requires nonempty code/detail")
        elif result is not None or error is not None:
            raise ValueError("nonterminal job cannot contain result/error")
        return cls(
            job_id,
            state,
            canonical_json_bytes(result) if result is not None else None,
            canonical_json_bytes(error) if error is not None else None,
        )

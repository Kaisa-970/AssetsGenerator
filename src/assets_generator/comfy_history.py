"""Correlate untrusted ComfyUI history with the exact durable submitted graph."""

from __future__ import annotations

from typing import Any

from .comfy_submission import ComfySubmissionUnknown
from .serialization import canonical_json_bytes


def validate_history(record: dict[str, Any], history: dict[str, Any]) -> dict[str, Any]:
    """Return a detached terminal observation, never a publishable Core result.

    Even matching history cannot attest custom-node/model identities or immutable
    output bytes. Those require deployment evidence and validated imports.
    """
    if record.get("phase") not in {"sending", "acknowledged"}:
        raise ComfySubmissionUnknown("ComfyUI prompt has not been authorized for submission")
    prompt_id = record["prompt_id"]
    if not history:
        raise ComfySubmissionUnknown("ComfyUI history missing; resubmission forbidden")
    if set(history) != {prompt_id}:
        raise ComfySubmissionUnknown("ComfyUI history refers to another prompt")
    entry = history[prompt_id]
    if not isinstance(entry, dict):
        raise ComfySubmissionUnknown("invalid ComfyUI history entry")
    prompt = entry.get("prompt")
    if (
        not isinstance(prompt, list)
        or len(prompt) < 3
        or prompt[1] != prompt_id
        or canonical_json_bytes(prompt[2]) != canonical_json_bytes(record["prompt"])
    ):
        raise ComfySubmissionUnknown("ComfyUI history differs from submitted workflow")
    status = entry.get("status")
    if (
        not isinstance(status, dict)
        or type(status.get("completed")) is not bool
        or not isinstance(status.get("messages"), list)
        or status.get("status_str") not in {"success", "error"}
    ):
        raise ComfySubmissionUnknown("invalid ComfyUI history status")
    if status["status_str"] == "success" and not status["completed"]:
        raise ComfySubmissionUnknown("ComfyUI success is not completed")
    outputs = entry.get("outputs")
    if not isinstance(outputs, dict) or any(
        node not in record["prompt"] or not isinstance(value, dict)
        for node, value in outputs.items()
    ):
        raise ComfySubmissionUnknown("invalid ComfyUI history output mapping")
    # Detach mutable caller-owned JSON; callers can persist these exact bytes.
    import json

    result: dict[str, Any] = json.loads(
        canonical_json_bytes(
            {
                "prompt_id": prompt_id,
                "state": "succeeded" if status["status_str"] == "success" else "failed",
                "submitted_prompt": record["prompt"],
                "status": status,
                "outputs": outputs,
                "provenance_scope": "composite_boundary_only",
            }
        )
    )
    return result

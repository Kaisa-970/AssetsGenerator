"""Outer service ownership for ComfyUI submission and query-only recovery.

These entry points operate on a claimed running job. They do not upload inputs,
import outputs, declare terminal success, or provide a complete service handler.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .comfy_http import ComfyClient
from .comfy_submission import ComfySubmissionJournal, ComfySubmissionUnknown
from .remote_protocol import RemoteRequest
from .remote_service_store import RemoteServiceStore


def submit_owned_prompt(
    store: RemoteServiceStore,
    request: RemoteRequest,
    journal_path: Path,
    client: ComfyClient,
    *,
    deployment: dict[str, Any],
    prompt: dict[str, Any],
) -> dict[str, Any]:
    """Persist outer authorization before sending; existing owners must recover."""
    if store.comfy_binding(request) is not None:
        raise ComfySubmissionUnknown("ComfyUI submission already authorized; query only")
    journal = ComfySubmissionJournal(journal_path)
    try:
        journal.prepare(request.submission_key, deployment=deployment, prompt=prompt)
        binding = journal.submission_binding(request.submission_key)
        if not store.authorize_comfy_submission(request, binding):
            raise ComfySubmissionUnknown("ComfyUI submission already authorized; query only")
        try:
            return client.submit(journal, request.submission_key)
        except Exception as error:
            # Includes local journal failures after durable authorization. No
            # exception in this window proves that upstream execution is absent.
            raise ComfySubmissionUnknown(
                "owned ComfyUI submission uncertain; query only"
            ) from error
    finally:
        journal.close()


def recover_owned_prompt(
    store: RemoteServiceStore,
    request: RemoteRequest,
    journal_path: Path,
    client: ComfyClient,
) -> dict[str, Any]:
    """Never prepare, recreate or send during recovery, including phase rollback."""
    journal = None
    try:
        binding = store.comfy_binding(request)
        if binding is None:
            raise ValueError("ComfyUI owner binding missing; cannot recover")
        journal = ComfySubmissionJournal(journal_path, expected_journal_id=binding["journal_id"])
        journal.verify_binding(binding)
        if journal.read(request.submission_key)["phase"] == "prepared":
            raise ValueError("ComfyUI authorization exists but journal is prepared")
        return client.observe(journal, request.submission_key)
    except Exception as error:
        raise ComfySubmissionUnknown("owned ComfyUI recovery blocked; never resubmit") from error
    finally:
        if journal is not None:
            journal.close()

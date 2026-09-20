"""Publish only failures correlated to the fixed ComfyUI submission."""

from __future__ import annotations

import json
from pathlib import Path

from .comfy_http import ComfyClient
from .comfy_service import recover_owned_prompt
from .comfy_submission import ComfySubmissionJournal, ComfySubmissionUnknown
from .remote_protocol import RemoteJob, RemoteRequest
from .remote_service_store import RemoteServiceStore


def finish_failure(
    owner: RemoteServiceStore, request: RemoteRequest, journal_path: Path, client: ComfyClient
) -> RemoteJob | None:
    """Return a verified failed job, or None for a successfully observed workflow."""
    journal = None
    try:
        job = owner.lookup(request)
        if job is not None and job.state == "failed":
            error = json.loads(job.error_json or b"{}")
            prefix = "Correlated upstream failure; evidence "
            if error.get("code") != "COMFY_EXECUTION_FAILED" or not error.get(
                "detail", ""
            ).startswith(prefix):
                raise ValueError("job has no correlated ComfyUI failure")
            evidence = json.loads(owner.get_blob(error["detail"][len(prefix) :]))
            return owner.publish_comfy_failure(request, **evidence)
        observation = recover_owned_prompt(owner, request, journal_path, client)
        if observation["state"] != "failed":
            return None
        binding = owner.comfy_binding(request)
        assert binding is not None
        journal = ComfySubmissionJournal(journal_path, expected_journal_id=binding["journal_id"])
        journal.verify_binding(binding)
        journal.observation(request.submission_key)
        raw = journal.db.execute(
            "SELECT body FROM observations WHERE submission_key=?", (request.submission_key,)
        ).fetchone()[0]
        return owner.publish_comfy_failure(
            request, record=journal.read(request.submission_key), history=json.loads(raw)
        )
    except Exception as error:
        raise ComfySubmissionUnknown("ComfyUI failure evidence unavailable; no replay") from error
    finally:
        if journal is not None:
            journal.close()

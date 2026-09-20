"""Outer service ownership for ComfyUI submission and query-only recovery.

These entry points operate on a claimed running job. They do not upload inputs,
import outputs, declare terminal success, or provide a complete service handler.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifact_store import LocalArtifactStore
from .comfy_http import ComfyClient
from .comfy_submission import ComfySubmissionJournal, ComfySubmissionUnknown
from .comfy_workflow import ComfyWorkflow
from .models import ArtifactRef
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


def finish_owned_image(
    owner: RemoteServiceStore,
    request: RemoteRequest,
    journal_path: Path,
    client: ComfyClient,
    store: LocalArtifactStore,
    *,
    node: str,
    index: int,
    mode: str,
) -> ArtifactRef:
    """Query original prompt, import once and fix the composite result evidence.

    Return the evidence Artifact; publication and job terminal transition remain
    explicit. Existing completed evidence can be read entirely offline.
    """
    from .comfy_import import import_image
    from .comfy_result import fix_image_result, recover_image_result

    journal = None
    try:
        if owner.comfy_result(request) is not None:
            ref = recover_image_result(owner, request, store)
            if store.read_structured(ref)["output_mapping"] != {
                "node": node,
                "index": index,
                "mode": mode,
            }:
                raise ValueError("ComfyUI output mapping changed")
            return ref
        observation = recover_owned_prompt(owner, request, journal_path, client)
        if observation["state"] != "succeeded":
            raise ValueError("ComfyUI has no successful output to import")
        binding = owner.comfy_binding(request)
        assert binding is not None
        journal = ComfySubmissionJournal(journal_path, expected_journal_id=binding["journal_id"])
        journal.verify_binding(binding)
        # Persist intent outside the internal journal. If its receipt later goes
        # missing, an authorized import cannot be mistaken for the first import.
        fresh = owner.authorize_comfy_import(
            request,
            {
                "store": str(store.root),
                "node": node,
                "index": index,
                "mode": mode,
                "submission": binding,
            },
        )
        import_image(
            client,
            journal,
            store,
            request.submission_key,
            node=node,
            index=index,
            mode=mode,
            recovery_only=not fresh,
        )
        return fix_image_result(owner, request, journal, store, node=node, index=index, mode=mode)
    except Exception as error:
        raise ComfySubmissionUnknown("ComfyUI image completion blocked; no replay") from error
    finally:
        if journal is not None:
            journal.close()


def start_owned_image(
    owner: RemoteServiceStore,
    request: RemoteRequest,
    journal_path: Path,
    client: ComfyClient,
    store: LocalArtifactStore,
    workflow: ComfyWorkflow,
    *,
    images: dict[str, ArtifactRef],
    parameters: dict[str, Any],
    deployment_claims: dict[str, Any],
) -> dict[str, Any]:
    """First execution only: upload trusted inputs, bind the graph and submit once.

    Caller must claim the service job first. Recovery uses finish_owned_image;
    it never invokes this entry point or repeats uploads.
    """
    from .comfy_upload import upload_image

    if owner.comfy_binding(request) is not None:
        raise ComfySubmissionUnknown("ComfyUI job already submitted; use completion recovery")
    job = owner.lookup(request)
    if job is None or job.state != "running":
        raise ValueError("ComfyUI start requires a claimed running job")
    if set(images) != set(workflow.image_targets):
        raise ValueError("ComfyUI images differ from declared inputs")
    workflow.spec.normalize_parameters(parameters)
    receipts = {name: upload_image(client, store, ref) for name, ref in images.items()}
    bound = workflow.bind(parameters, images=receipts, endpoint=client.endpoint)
    return submit_owned_prompt(
        owner,
        request,
        journal_path,
        client,
        deployment={
            "endpoint": client.endpoint,
            "workflow": bound,
            "claims": deployment_claims,
            "internal_verification": "unverified",
        },
        prompt=bound["prompt"],
    )

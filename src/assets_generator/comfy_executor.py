"""Explicit service dispatch for ComfyUI DAG jobs; no implicit polling or replay."""

from __future__ import annotations

from pathlib import Path

from .artifact_store import LocalArtifactStore
from .comfy_profile import ComfyImageProfile
from .remote_protocol import RemoteJob
from .remote_service_store import RemoteServiceStore


def execute_next_image(
    profile: ComfyImageProfile,
    owner: RemoteServiceStore,
    journal_path: Path,
    store: LocalArtifactStore,
) -> RemoteJob | None:
    """Claim one queued request, submit once, then observe once.

    An exception leaves the durable running claim intact. Resume via
    recover_image_job, never by invoking first execution again.
    """
    if owner.identity != profile.identity:
        raise ValueError("ComfyUI executor profile differs from service identity")
    request = owner.claim_next_queued()
    if request is None:
        return None
    profile.start_dag(owner, request, journal_path, store)
    return profile.finish(owner, request, journal_path, store)


def recover_image_job(
    profile: ComfyImageProfile,
    owner: RemoteServiceStore,
    key: str,
    journal_path: Path,
    store: LocalArtifactStore,
) -> RemoteJob:
    """Observe the existing job only; never claim, import inputs or submit."""
    if owner.identity != profile.identity:
        raise ValueError("ComfyUI executor profile differs from service identity")
    request = owner.request_for(key)
    if request is None:
        raise ValueError("ComfyUI recovery job missing")
    job = owner.lookup(request)
    if job is None or job.state == "queued":
        raise ValueError("ComfyUI recovery requires an already claimed job")
    return profile.finish(owner, request, journal_path, store)

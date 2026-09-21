"""Serial opt-in service executor; unresolved jobs only recover their original key."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from threading import Event

from .artifact_store import LocalArtifactStore
from .sam3d_bridge import Sam3DBridge
from .sam3d_http import Sam3DUnknown
from .sam3d_service_store import Sam3DServiceStore


def tick(
    owner: Sam3DServiceStore, bridge: Sam3DBridge, artifacts: LocalArtifactStore
) -> dict[str, str]:
    # Traverse every page: a running job may be older than the newest 100 records.
    before = None
    running = []
    queued = False
    while True:
        page = owner.list_jobs(before=before)
        for job in page["jobs"]:
            if job["state"] == "running":
                running.append(job["job_id"])
            queued |= job["state"] == "queued"
        before = page["next_before"]
        if before is None:
            break
    if len(running) > 1:
        raise ValueError("SAM3D service has multiple unresolved running jobs")
    try:
        if running:
            request = owner.request_for(running[0])
            if request is None:
                raise ValueError("SAM3D running request missing")
            job = bridge.recover(owner, request, artifacts)
        elif queued:
            job = bridge.start_next(owner, artifacts)
        else:
            return {"state": "idle"}
        return {"state": job.state, "job_id": job.job_id} if job else {"state": "idle"}
    except Sam3DUnknown:
        # Never release a claim or invoke start_next for an unresolved job.
        # Repeated GET lookup is safe; repeated POST is forbidden.
        return {"state": "uncertain", "detail": "original task retained; no resubmission"}


def run_loop(
    owner: Sam3DServiceStore,
    bridge: Sam3DBridge,
    artifacts: LocalArtifactStore,
    *,
    stop: Event,
    interval: float = 5,
    report: Callable[[str], None] = print,
) -> None:
    if not math.isfinite(interval) or not 0.1 <= interval <= 60:
        raise ValueError("poll interval must be finite and between 0.1 and 60 seconds")
    previous = None
    while not stop.is_set():
        status = tick(owner, bridge, artifacts)
        message = json.dumps(status, sort_keys=True)
        if message != previous:
            report(message)
            previous = message
        stop.wait(interval)

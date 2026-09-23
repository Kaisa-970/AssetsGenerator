"""Opt-in serial shape execution, preserving the existing durable claim boundary."""

from __future__ import annotations

import json
from collections.abc import Callable
from threading import Event

from .errors import DeploymentIdentityError, ErrorCode
from .remote_service_store import RemoteServiceStore
from .remote_service_worker import ServiceHandler, execute_next_service_job


def run_loop(
    store: RemoteServiceStore,
    handler: ServiceHandler,
    *,
    stop: Event,
    interval: float = 1.0,
    report: Callable[[str], None] | None = None,
) -> None:
    """Do not replay running jobs or retry uncertain execution/storage exceptions.

    SIGTERM/SIGINT request draining the current call, then stop claiming. The
    existing process worker owns timeout/exit verification. An external hard
    kill leaves durable running/worker evidence intact for explicit resolution.
    """
    if report is None:

        def report(message: str) -> None:
            print(message, flush=True)

    while not stop.is_set():
        # Idle polls inspect the queue only; deployment checks stay in handler
        # before/after an actually claimed inference, not on every empty poll.
        job = execute_next_service_job(store, handler)
        if job is None:
            stop.wait(interval)
            continue
        report(json.dumps({"job_id": job.job_id, "state": job.state}))
        if (
            job.state == "failed"
            and json.loads(job.error_json or "{}").get("code") == ErrorCode.DEPLOYMENT_INVALID.value
        ):
            raise DeploymentIdentityError(
                "deployment identity invalid; worker stopped claiming tasks; "
                "administrator must verify deployment before restarting"
            )

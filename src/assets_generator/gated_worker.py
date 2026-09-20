"""Shared gated subprocess lifecycle; durable ownership belongs to the caller."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .errors import ErrorCode, PipelineError
from .runtime import utc_now
from .serialization import cache_key
from .workbench_models import ProcessIdentity, ProcessObservation
from .workbench_process import GatedLaunchChannel, LinuxProcessProbe
from .worker import ProcessJobRequest, WorkerJob


@dataclass(frozen=True)
class GatedWorkerCallbacks:
    """Callbacks return only after their evidence is durably committed.

    prepared receives the fresh job ID and launch request digest. No callback
    requires a child BuildRun: the owner maps evidence onto its own attempt.
    authorized must persist authorization before returning; raising prevents
    release. exited is called only after the entire process group is empty.
    """

    prepared: Callable[[str, str], None]
    identified: Callable[[ProcessIdentity], None]
    authorized: Callable[[], None]
    exited: Callable[[ProcessObservation, int], None]


class ProcessProbe(Protocol):
    def observe(self, identity: ProcessIdentity) -> ProcessObservation: ...


def run_gated_process(
    request: ProcessJobRequest,
    callbacks: GatedWorkerCallbacks,
    *,
    channel_factory: Callable[..., GatedLaunchChannel] = GatedLaunchChannel,
    probe: ProcessProbe | None = None,
) -> WorkerJob:
    """Run one isolated offline Backend command after durable authorization."""
    if (
        isinstance(request.timeout_seconds, bool)
        or not math.isfinite(request.timeout_seconds)
        or request.timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be finite and positive")
    job_id = f"job_{uuid.uuid4().hex}"
    started = utc_now()
    callbacks.prepared(
        job_id,
        cache_key(
            {
                "command": request.command,
                "cwd": str(request.cwd),
                "timeout": request.timeout_seconds,
                "offline": True,
            }
        ),
    )
    environment = {
        **os.environ,
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
    }
    process_probe = probe if probe is not None else LinuxProcessProbe()
    with (
        tempfile.TemporaryFile(mode="w+b") as stdout,
        tempfile.TemporaryFile(mode="w+b") as stderr,
    ):
        channel = channel_factory(
            request.command, cwd=request.cwd, env=environment, stdout=stdout, stderr=stderr
        )
        try:
            identity = channel.identify()
            callbacks.identified(identity)
            callbacks.authorized()
            channel.release()
            timed_out = False
            try:
                code = channel.wait(request.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                observation = process_probe.observe(identity)
                if observation.observed_identity != identity or observation.result != "alive":
                    raise PipelineError(
                        ErrorCode.BACKEND_TIMEOUT,
                        "timeout with uncertain process ownership; retry blocked",
                    ) from None
                os.killpg(identity.pgid, signal.SIGKILL)
                code = channel.wait(10)
            observation = process_probe.observe(identity)
            if observation.result != "exited":
                raise PipelineError(
                    ErrorCode.BACKEND_FAILED,
                    "backend process group remains active or unverified",
                )
            callbacks.exited(observation, code)
            stdout.seek(0)
            stderr.seek(0)
            output = stdout.read().decode("utf-8", errors="replace")
            error = stderr.read().decode("utf-8", errors="replace")
            if timed_out or code:
                raise PipelineError(
                    ErrorCode.BACKEND_TIMEOUT if timed_out else ErrorCode.BACKEND_FAILED,
                    error or output or "backend command failed",
                )
            return WorkerJob(
                job_id,
                request.idempotency_key,
                "succeeded",
                started,
                started,
                utc_now(),
                code,
                None,
                output,
                error,
            )
        finally:
            channel.close()

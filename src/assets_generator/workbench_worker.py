"""Gate all Backend runner commands within an owned workbench adapter call."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import tempfile
import uuid
from typing import TYPE_CHECKING

from .errors import ErrorCode, PipelineError
from .runtime import utc_now
from .serialization import cache_key
from .workbench_models import WorkerExecution
from .workbench_process import GatedLaunchChannel, LinuxProcessProbe
from .workbench_state import AuthorizeLaunch, LauncherIdentified, WorkerExited, WorkerPrepared
from .worker import ProcessJobRequest, WorkerJob

if TYPE_CHECKING:
    from .workbench_engine import WorkbenchEngine


class WorkbenchProcessWorker:
    def __init__(self, engine: WorkbenchEngine, run_id: str, stage_id: str) -> None:
        self.engine, self.run_id, self.stage_id = engine, run_id, stage_id

    def run(self, request: ProcessJobRequest) -> WorkerJob:
        if (
            isinstance(request.timeout_seconds, bool)
            or not math.isfinite(request.timeout_seconds)
            or request.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        run = self.engine.repository.load(self.run_id)
        assert run.workbench is not None
        attempt = run.workbench.stage_states[self.stage_id].current()
        assert attempt.child_run_id is not None
        job_id = f"job_{uuid.uuid4().hex}"
        started = utc_now()
        execution = WorkerExecution(
            job_id,
            attempt.child_run_id,
            cache_key(
                {
                    "command": request.command,
                    "cwd": str(request.cwd),
                    "timeout": request.timeout_seconds,
                    "offline": True,
                }
            ),
        )
        self.engine._event(self.run_id, self.stage_id, WorkerPrepared(execution))
        environment = {
            **os.environ,
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
        }
        probe = LinuxProcessProbe()
        with (
            tempfile.TemporaryFile(mode="w+b") as stdout,
            tempfile.TemporaryFile(mode="w+b") as stderr,
        ):
            channel = GatedLaunchChannel(
                request.command, cwd=request.cwd, env=environment, stdout=stdout, stderr=stderr
            )
            try:
                identity = channel.identify()
                self.engine._event(self.run_id, self.stage_id, LauncherIdentified(identity))
                # The authorization event is committed before its queue-only effect.
                self.engine._event(self.run_id, self.stage_id, AuthorizeLaunch())
                channel.release()
                timed_out = False
                try:
                    code = channel.wait(request.timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    observation = probe.observe(identity)
                    if observation.observed_identity != identity or observation.result != "alive":
                        raise PipelineError(
                            ErrorCode.BACKEND_TIMEOUT,
                            "timeout with uncertain process ownership; retry blocked",
                        ) from None
                    os.killpg(identity.pgid, signal.SIGKILL)
                    code = channel.wait(10)
                observation = probe.observe(identity)
                if observation.result != "exited":
                    raise PipelineError(
                        ErrorCode.BACKEND_FAILED,
                        "backend process group remains active or unverified",
                    )
                self.engine._event(self.run_id, self.stage_id, WorkerExited(observation, code))
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

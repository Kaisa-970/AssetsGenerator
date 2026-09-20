"""Gate all Backend runner commands within an owned workbench adapter call."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .gated_worker import GatedWorkerCallbacks, run_gated_process
from .workbench_models import ProcessIdentity, ProcessObservation, WorkerExecution
from .workbench_process import GatedLaunchChannel, LinuxProcessProbe
from .workbench_state import AuthorizeLaunch, LauncherIdentified, WorkerExited, WorkerPrepared
from .worker import ProcessJobRequest, WorkerJob

if TYPE_CHECKING:
    from .workbench_engine import WorkbenchEngine


class WorkbenchProcessWorker:
    def __init__(self, engine: WorkbenchEngine, run_id: str, stage_id: str) -> None:
        self.engine, self.run_id, self.stage_id = engine, run_id, stage_id

    def run(self, request: ProcessJobRequest) -> WorkerJob:
        run = self.engine.repository.load(self.run_id)
        assert run.workbench is not None
        attempt = run.workbench.stage_states[self.stage_id].current()
        assert attempt.child_run_id is not None
        child_run_id = attempt.child_run_id

        def prepared(job_id: str, request_digest: str) -> None:
            self.engine._event(
                self.run_id,
                self.stage_id,
                WorkerPrepared(WorkerExecution(job_id, child_run_id, request_digest)),
            )

        def identified(identity: ProcessIdentity) -> None:
            self.engine._event(self.run_id, self.stage_id, LauncherIdentified(identity))

        def authorized() -> None:
            self.engine._event(self.run_id, self.stage_id, AuthorizeLaunch())

        def exited(observation: ProcessObservation, code: int) -> None:
            self.engine._event(self.run_id, self.stage_id, WorkerExited(observation, code))

        return run_gated_process(
            request,
            GatedWorkerCallbacks(prepared, identified, authorized, exited),
            channel_factory=GatedLaunchChannel,
            probe=LinuxProcessProbe(),
        )

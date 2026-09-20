"""Gated subprocess evidence attached to the current DAG attempt."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .gated_worker import GatedWorkerCallbacks, run_gated_process
from .models import BuildRun
from .process_admission import admit_compute
from .workbench_models import ProcessIdentity, ProcessObservation, WorkerExecution
from .worker import ProcessJobRequest, WorkerJob

if TYPE_CHECKING:
    from .dag_engine import DagEngine


class DagProcessWorker:
    def __init__(self, engine: DagEngine, run: BuildRun, node_id: str):
        self.engine, self.run_record, self.node_id = engine, run, node_id

    def run(self, request: ProcessJobRequest) -> WorkerJob:
        run = self.run_record
        assert run.dag is not None
        attempt = run.dag.node_states[self.node_id].current()
        admit_compute(self.engine.repository, self.engine.probe)
        execution: WorkerExecution

        def prepared(job_id: str, launch_digest: str) -> None:
            nonlocal execution
            execution = WorkerExecution(
                job_id,
                attempt.child_registration.child_run_id
                if attempt.child_registration
                else run.run_id,
                launch_digest,
            )
            attempt.worker_executions.append(execution)
            self.engine._save(run)

        def identified(identity: ProcessIdentity) -> None:
            execution.host_id = identity.host_id
            execution.boot_id = identity.boot_id
            execution.pid = identity.pid
            execution.pgid = identity.pgid
            execution.starttime_ticks = identity.starttime_ticks
            execution.launch_phase = "identity_recorded"
            self.engine._save(run)

        def authorized() -> None:
            execution.launch_phase = "release_authorized"
            self.engine._save(run)

        def exited(observation: ProcessObservation, code: int) -> None:
            execution.last_probe = observation
            execution.exit_code = code
            execution.launch_phase = "exit_observed"
            self.engine._save(run)

        return run_gated_process(
            request,
            GatedWorkerCallbacks(prepared, identified, authorized, exited),
            probe=self.engine.probe,
        )

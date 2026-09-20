"""Gated independent-environment commands with durable service job ownership."""

from __future__ import annotations

from .gated_worker import GatedWorkerCallbacks, run_gated_process
from .remote_protocol import RemoteRequest
from .remote_service_store import RemoteServiceStore
from .serialization import canonical_json_bytes
from .workbench_models import ProcessIdentity, ProcessObservation, WorkerExecution
from .worker import ProcessJobRequest, WorkerJob


class ServiceProcessWorker:
    def __init__(self, store: RemoteServiceStore, owner: RemoteRequest):
        self.store, self.owner = store, owner

    def run(self, request: ProcessJobRequest) -> WorkerJob:
        record: WorkerExecution
        previous: bytes | None = None

        def save() -> None:
            nonlocal previous
            body = canonical_json_bytes(record)
            self.store.save_worker(self.owner, body, previous=previous)
            previous = body

        def prepared(job_id: str, launch_digest: str) -> None:
            nonlocal record
            record = WorkerExecution(job_id, self.owner.submission_key, launch_digest)
            save()

        def identified(identity: ProcessIdentity) -> None:
            record.host_id, record.boot_id = identity.host_id, identity.boot_id
            record.pid, record.pgid = identity.pid, identity.pgid
            record.starttime_ticks = identity.starttime_ticks
            record.launch_phase = "identity_recorded"
            save()

        def authorized() -> None:
            record.launch_phase = "release_authorized"
            save()

        def exited(observation: ProcessObservation, code: int) -> None:
            record.last_probe, record.exit_code = observation, code
            record.launch_phase = "exit_observed"
            save()

        return run_gated_process(
            request, GatedWorkerCallbacks(prepared, identified, authorized, exited)
        )

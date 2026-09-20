"""Durable remote submission journal under an existing exclusive repository lock.

Not a DAG adapter: callers must not infer local-process admission from this journal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .remote_http import RemoteJobClient, RemoteTransportUnknown
from .remote_protocol import RemoteJob, RemoteRequest
from .serialization import canonical_json_bytes, read_json, sha256_bytes
from .workbench_persistence import WorkbenchRepository


class RemoteSubmission:
    def __init__(self, repository: WorkbenchRepository, client: RemoteJobClient):
        repository._ready()
        self.repository = repository
        self.client = client

    def _path(self, request: RemoteRequest) -> Path:
        key = sha256_bytes(request.submission_key.encode()).split(":")[1]
        return self.repository.directory / "remote_submissions" / f"{key}.json"

    def _reservation(self, request: RemoteRequest) -> Path:
        return self.repository.directory / "remote_reservations" / self._path(request).name

    def _reservation_value(self, request: RemoteRequest) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "endpoint": self.client.endpoint,
            "request": request.to_dict(),
        }

    def _load(self, request: RemoteRequest) -> dict[str, Any]:
        if read_json(self._reservation(request)) != self._reservation_value(request):
            raise ValueError("remote reservation identity conflict")
        record = read_json(self._path(request))
        if (
            set(record) != {"schema_version", "endpoint", "request", "phase", "job"}
            or record["schema_version"] != "1"
            or record["endpoint"] != self.client.endpoint
            or record["request"] != request.to_dict()
        ):
            raise ValueError("remote submission identity conflict or corrupt journal")
        if record["phase"] not in {"prepared", "authorized", "observed"}:
            raise ValueError("invalid remote submission phase")
        if record["phase"] == "observed":
            RemoteJob.parse(record["job"], request)
        elif record["job"] is not None:
            raise ValueError("unobserved submission cannot contain a job")
        return record

    def _write(
        self, request: RemoteRequest, record: dict[str, Any], *, exclusive: bool = False
    ) -> None:
        self.repository._mutate(
            lambda: self.repository.io.write(
                self._path(request),
                canonical_json_bytes(record),
                exclusive=exclusive,
            )
        )

    def prepare(self, request: RemoteRequest) -> None:
        with self.repository._command_lock:
            self.repository._ready()
            if self._path(request).exists():
                self._load(request)
                return
            reservation = self._reservation(request)
            if reservation.exists():
                raise ValueError("reserved remote journal missing; refusing to recreate")
            self.repository._mutate(
                lambda: self.repository.io.write(
                    reservation,
                    canonical_json_bytes(self._reservation_value(request)),
                    exclusive=True,
                )
            )
            self._write(
                request,
                {
                    "schema_version": "1",
                    "endpoint": self.client.endpoint,
                    "request": request.to_dict(),
                    "phase": "prepared",
                    "job": None,
                },
                exclusive=True,
            )

    def _observe(self, request: RemoteRequest, record: dict[str, Any], job: RemoteJob) -> RemoteJob:
        wire = {key: value for key, value in request.to_dict().items() if key != "payload"}
        wire.update(
            protocol_version="1",
            job_id=job.job_id,
            state=job.state,
            result=json.loads(job.result_json) if job.result_json is not None else None,
            error=json.loads(job.error_json) if job.error_json is not None else None,
        )
        previous = record["job"]
        RemoteJob.parse(wire, request, expected_job_id=previous["job_id"] if previous else None)
        if previous and previous["state"] in {"succeeded", "failed"} and previous != wire:
            raise ValueError("remote terminal result changed")
        if previous and previous["state"] == "running" and job.state == "queued":
            raise ValueError("remote job state regressed")
        if previous != wire:
            self._write(request, {**record, "phase": "observed", "job": wire})
        return job

    def recover(self, request: RemoteRequest) -> RemoteJob | None:
        """Read the original job; never submit, including after a lookup 404."""
        with self.repository._command_lock:
            self.repository._ready()
            record = self._load(request)
            previous = record["job"]
            job = (
                self.client.query(request, previous["job_id"])
                if previous
                else self.client.lookup(request)
            )
            return None if job is None else self._observe(request, record, job)

    def submit(self, request: RemoteRequest) -> RemoteJob:
        with self.repository._command_lock:
            self.prepare(request)
            record = self._load(request)
            if record["phase"] != "prepared":
                job = self.recover(request)
                if job is None:
                    raise RemoteTransportUnknown(
                        "authorized submission not found; no automatic resubmission"
                    )
                return job
            # A crash at any later point may have delivered the POST. Never
            # return this record to prepared, even if no job ID was received.
            self._write(request, {**record, "phase": "authorized"})
            job = self.client.submit(request)
            return self._observe(request, self._load(request), job)

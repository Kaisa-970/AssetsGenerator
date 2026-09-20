"""Service-side durable job ownership; no inference, HTTP listener or automatic replay."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .remote_protocol import RemoteIdentity, RemoteJob, RemoteOutput, RemoteRequest, _identifier
from .serialization import canonical_json_bytes, sha256_bytes
from .workbench_models import ProcessObservation


class RemoteServiceStore:
    """SQLite transactions fix submission keys and claim queued jobs exactly once.

    Running jobs remain running after reopen. A service supervisor must resolve
    their worker identity; reopening this store never authorizes inference again.
    """

    def __init__(self, path: Path, identity: RemoteIdentity):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.identity = identity
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS service (id INTEGER PRIMARY KEY, body BLOB NOT NULL)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS jobs "
                "(key TEXT PRIMARY KEY, request BLOB NOT NULL, job BLOB NOT NULL)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS blobs (digest TEXT PRIMARY KEY, body BLOB NOT NULL)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS process_workers "
                "(key TEXT PRIMARY KEY, body BLOB NOT NULL)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS process_exits "
                "(key TEXT PRIMARY KEY, worker BLOB NOT NULL, observation BLOB NOT NULL)"
            )
            body = canonical_json_bytes(identity)
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute("SELECT body FROM service WHERE id=1").fetchone()
            if existing is None:
                self.db.execute("INSERT INTO service VALUES (1, ?)", (body,))
            elif existing[0] != body:
                raise ValueError("service storage identity mismatch")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.close()
            raise

    def close(self) -> None:
        with self._lock:
            self.db.close()

    def put_blob(self, data: bytes, digest: str, *, max_bytes: int = 128 * 1024 * 1024) -> None:
        if (
            not isinstance(data, bytes)
            or not data
            or len(data) > max_bytes
            or sha256_bytes(data) != digest
        ):
            raise ValueError("invalid service input blob")
        with self._lock:
            self.db.execute("INSERT OR IGNORE INTO blobs VALUES (?, ?)", (digest, data))
            if self.get_blob(digest) != data:
                raise ValueError("existing service blob differs")

    def get_blob(self, digest: str) -> bytes:
        with self._lock:
            row = self.db.execute("SELECT body FROM blobs WHERE digest=?", (digest,)).fetchone()
            if row is None or sha256_bytes(row[0]) != digest:
                raise ValueError("service blob missing/corrupt")
            return bytes(row[0])

    def _request(self, request: RemoteRequest) -> None:
        if request.identity != self.identity:
            raise ValueError("request targets another service identity")

    def _wire(self, request: RemoteRequest) -> dict[str, Any]:
        wire = {k: v for k, v in request.to_dict().items() if k != "payload"}
        wire.update(
            protocol_version="1",
            job_id=request.submission_key,
            state="queued",
            result=None,
            error=None,
        )
        return wire

    def submit(self, request: RemoteRequest) -> RemoteJob:
        self._request(request)
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT request, job FROM jobs WHERE key=?", (request.submission_key,)
                ).fetchone()
                if row is None:
                    wire = self._wire(request)
                    self.db.execute(
                        "INSERT INTO jobs VALUES (?, ?, ?)",
                        (
                            request.submission_key,
                            canonical_json_bytes(request.to_dict()),
                            canonical_json_bytes(wire),
                        ),
                    )
                else:
                    if row[0] != canonical_json_bytes(request.to_dict()):
                        raise ValueError("submission key conflict")
                    wire = json.loads(row[1])
                job = RemoteJob.parse(wire, request, expected_job_id=request.submission_key)
                self.db.execute("COMMIT")
                return job
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def lookup(self, request: RemoteRequest) -> RemoteJob | None:
        self._request(request)
        with self._lock:
            row = self.db.execute(
                "SELECT request, job FROM jobs WHERE key=?", (request.submission_key,)
            ).fetchone()
            if row is None:
                return None
            if row[0] != canonical_json_bytes(request.to_dict()):
                raise ValueError("submission key conflict")
            return RemoteJob.parse(
                json.loads(row[1]), request, expected_job_id=request.submission_key
            )

    def transition(
        self,
        request: RemoteRequest,
        *,
        expected: str,
        state: str,
        result: dict[str, Any] | None = None,
        error: dict[str, str] | None = None,
    ) -> RemoteJob:
        """CAS transition: only queued→running and running→terminal are allowed."""
        if (expected, state) not in {
            ("queued", "running"),
            ("running", "succeeded"),
            ("running", "failed"),
        }:
            raise ValueError("invalid service job transition")
        _identifier(request.submission_key, "submission_key")
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                previous = self.lookup(request)
                if previous is None or previous.state != expected:
                    raise ValueError("service job state conflict")
                wire = self._wire(request)
                wire.update(state=state, result=result, error=error)
                job = RemoteJob.parse(wire, request)
                if job.state == "succeeded":
                    self._validate_outputs(job)
                self.db.execute(
                    "UPDATE jobs SET job=? WHERE key=?",
                    (canonical_json_bytes(wire), request.submission_key),
                )
                self.db.execute("COMMIT")
                return job
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def _validate_outputs(self, job: RemoteJob) -> None:
        raw = json.loads(job.result_json or b"{}")
        if set(raw) != {"outputs"} or not isinstance(raw["outputs"], list):
            raise ValueError("service success requires output descriptors")
        for item in raw["outputs"]:
            if not isinstance(item, dict) or not isinstance(item.get("output_id"), str):
                raise ValueError("invalid service output descriptor")
            output = RemoteOutput.from_job(job, item["output_id"])
            data = self.get_blob(output.blob_digest)
            if len(data) != output.byte_length:
                raise ValueError("service output length mismatch")

    def download(self, request: RemoteRequest, output_id: str) -> tuple[RemoteOutput, bytes]:
        """Serve only the bytes named by the immutable successful job result."""
        with self._lock:
            job = self.lookup(request)
            if job is None:
                raise ValueError("service job missing")
            output = RemoteOutput.from_job(job, output_id)
            data = self.get_blob(output.blob_digest)
            if len(data) != output.byte_length:
                raise ValueError("service output length mismatch")
            return output, data

    def request_for(self, key: str) -> RemoteRequest | None:
        """Resolve the protocol's job ID/submission key to its immutable request."""
        _identifier(key, "job key")
        with self._lock:
            row = self.db.execute("SELECT request FROM jobs WHERE key=?", (key,)).fetchone()
            if row is None:
                return None
            raw = json.loads(row[0])
            request = RemoteRequest.create(self.identity, key, raw["payload"])
            if canonical_json_bytes(request.to_dict()) != row[0]:
                raise ValueError("corrupt service request")
            return request

    def worker_record(self, request: RemoteRequest) -> bytes | None:
        self._request(request)
        with self._lock:
            row = self.db.execute(
                "SELECT body FROM process_workers WHERE key=?", (request.submission_key,)
            ).fetchone()
            return bytes(row[0]) if row else None

    def save_worker(self, request: RemoteRequest, body: bytes, *, previous: bytes | None) -> None:
        """CAS callback evidence; one unfinished process slot per service database."""
        from .workbench_models import WorkerExecution, _decode

        record = _decode(WorkerExecution, json.loads(body))
        if record.child_run_id != request.submission_key:
            raise ValueError("service worker owner mismatch")
        phases = ["prepared", "identity_recorded", "release_authorized", "exit_observed"]
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                job = self.lookup(request)
                if job is None or job.state != "running":
                    raise ValueError("worker requires running service job")
                if self.worker_record(request) != previous:
                    raise ValueError("service worker revision conflict")
                if previous is None:
                    if record.launch_phase != "prepared":
                        raise ValueError("worker must begin prepared")
                    for key, raw in self.db.execute("SELECT key, body FROM process_workers"):
                        existing = _decode(WorkerExecution, json.loads(raw))
                        if existing.launch_phase != "exit_observed" and not self._released_worker(
                            key, raw
                        ):
                            raise ValueError("service process slot remains occupied")
                else:
                    old = _decode(WorkerExecution, json.loads(previous))
                    if (record.job_id, record.child_run_id, record.launch_request_digest) != (
                        old.job_id,
                        old.child_run_id,
                        old.launch_request_digest,
                    ) or phases.index(record.launch_phase) != phases.index(old.launch_phase) + 1:
                        raise ValueError("invalid worker transition")
                    if old.launch_phase != "prepared" and record.identity() != old.identity():
                        raise ValueError("worker process identity changed")
                if record.launch_phase != "prepared":
                    record.identity()
                if record.launch_phase == "exit_observed" and (
                    record.last_probe is None
                    or record.last_probe.result != "exited"
                    or record.exit_code is None
                ):
                    raise ValueError("worker exit requires observed empty process group")
                self.db.execute(
                    "INSERT OR REPLACE INTO process_workers VALUES (?, ?)",
                    (request.submission_key, body),
                )
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def _released_worker(self, key: str, worker: bytes) -> bool:
        from .workbench_models import ProcessObservation, _decode

        row = self.db.execute(
            "SELECT worker, observation FROM process_exits WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            return False
        observation = _decode(ProcessObservation, json.loads(row[1]))
        if row[0] != worker or observation.result != "exited":
            raise ValueError("invalid service process exit evidence")
        return True

    def observe_worker(self, request: RemoteRequest) -> ProcessObservation:
        """Probe the saved identity; persist only confirmed exit, without inventing a code."""
        from .workbench_models import ProcessObservation, WorkerExecution, _decode
        from .workbench_process import LinuxProcessProbe

        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                raw = self.worker_record(request)
                if raw is None:
                    raise ValueError("service worker identity missing")
                worker = _decode(WorkerExecution, json.loads(raw))
                if self._released_worker(request.submission_key, raw):
                    row = self.db.execute(
                        "SELECT observation FROM process_exits WHERE key=?",
                        (request.submission_key,),
                    ).fetchone()
                    observation = _decode(ProcessObservation, json.loads(row[0]))
                    assert isinstance(observation, ProcessObservation)
                else:
                    observation = LinuxProcessProbe().observe(worker.identity())
                    if observation.result == "exited":
                        self.db.execute(
                            "INSERT INTO process_exits VALUES (?, ?, ?)",
                            (request.submission_key, raw, canonical_json_bytes(observation)),
                        )
                self.db.execute("COMMIT")
                assert isinstance(observation, ProcessObservation)
                return observation
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

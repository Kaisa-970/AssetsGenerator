from __future__ import annotations

import subprocess
import threading
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from .errors import ErrorCode, PipelineError
from .runtime import utc_now

JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


@dataclass(frozen=True)
class ProcessJobRequest:
    command: list[str]
    cwd: Path
    timeout_seconds: float
    idempotency_key: str


@dataclass(frozen=True)
class WorkerJob:
    job_id: str
    idempotency_key: str
    status: JobStatus
    submitted_at: str
    started_at: str | None = None
    finished_at: str | None = None
    return_code: int | None = None
    error_code: str | None = None
    stdout: str = ""
    stderr: str = ""


class LocalProcessWorker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, WorkerJob] = {}
        self._idempotency: dict[str, str] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._done: dict[str, threading.Event] = {}
        self._cancel_requested: set[str] = set()

    def submit(self, request: ProcessJobRequest) -> str:
        if request.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        with self._lock:
            existing = self._idempotency.get(request.idempotency_key)
            if existing is not None:
                return existing
            job_id = f"job_{uuid.uuid4().hex}"
            self._jobs[job_id] = WorkerJob(job_id, request.idempotency_key, "queued", utc_now())
            self._idempotency[request.idempotency_key] = job_id
            self._done[job_id] = threading.Event()
        threading.Thread(target=self._execute, args=(job_id, request), daemon=True).start()
        return job_id

    def get(self, job_id: str) -> WorkerJob:
        with self._lock:
            try:
                return replace(self._jobs[job_id])
            except KeyError as error:
                raise KeyError(f"unknown worker job: {job_id}") from error

    def wait(self, job_id: str) -> WorkerJob:
        with self._lock:
            event = self._done.get(job_id)
        if event is None:
            raise KeyError(f"unknown worker job: {job_id}")
        event.wait()
        return self.get(job_id)

    def cancel(self, job_id: str) -> WorkerJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"unknown worker job: {job_id}")
            if job.status in {"succeeded", "failed", "cancelled"}:
                return replace(job)
            self._cancel_requested.add(job_id)
            process = self._processes.get(job_id)
            if process is None:
                self._jobs[job_id] = replace(
                    job,
                    status="cancelled",
                    finished_at=utc_now(),
                    error_code=ErrorCode.CANCELLED.value,
                )
                self._done[job_id].set()
                return replace(self._jobs[job_id])
        process.terminate()
        return self.wait(job_id)

    def run(self, request: ProcessJobRequest) -> WorkerJob:
        job_id = self.submit(request)
        try:
            job = self.wait(job_id)
        except KeyboardInterrupt:
            self.cancel(job_id)
            raise
        if job.status == "succeeded":
            return job
        code = ErrorCode(job.error_code or ErrorCode.BACKEND_FAILED.value)
        detail = job.stderr.strip() or job.stdout.strip() or f"worker job {job.status}"
        raise PipelineError(
            code,
            detail,
            retryable=code in {ErrorCode.BACKEND_TIMEOUT, ErrorCode.BACKEND_FAILED},
        )

    def _execute(self, job_id: str, request: ProcessJobRequest) -> None:
        stdout = ""
        stderr = ""
        return_code: int | None = None
        error_code: str | None = None
        status: JobStatus = "failed"
        with self._lock:
            if job_id in self._cancel_requested:
                current = self._jobs[job_id]
                if current.status != "cancelled":
                    self._jobs[job_id] = replace(
                        current,
                        status="cancelled",
                        finished_at=utc_now(),
                        error_code=ErrorCode.CANCELLED.value,
                    )
                self._done[job_id].set()
                return
            self._jobs[job_id] = replace(self._jobs[job_id], status="running", started_at=utc_now())
        try:
            process = subprocess.Popen(
                request.command,
                cwd=request.cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            with self._lock:
                self._processes[job_id] = process
                cancelled = job_id in self._cancel_requested
            if cancelled:
                process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=request.timeout_seconds)
                return_code = process.returncode
                with self._lock:
                    cancelled = job_id in self._cancel_requested
                if cancelled:
                    status = "cancelled"
                    error_code = ErrorCode.CANCELLED.value
                elif return_code == 0:
                    status = "succeeded"
                else:
                    status = "failed"
                    error_code = ErrorCode.BACKEND_FAILED.value
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                status = "failed"
                error_code = ErrorCode.BACKEND_TIMEOUT.value
                return_code = process.returncode
        except OSError as error:
            stderr = str(error)
            status = "failed"
            error_code = ErrorCode.BACKEND_UNAVAILABLE.value
        finally:
            with self._lock:
                self._processes.pop(job_id, None)
                current = self._jobs[job_id]
                if current.status == "cancelled":
                    self._done[job_id].set()
                else:
                    self._jobs[job_id] = replace(
                        current,
                        status=status,
                        finished_at=utc_now(),
                        return_code=return_code,
                        error_code=error_code,
                        stdout=stdout,
                        stderr=stderr,
                    )
                    self._done[job_id].set()

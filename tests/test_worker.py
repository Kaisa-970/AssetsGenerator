from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from assets_generator.errors import ErrorCode, PipelineError
from assets_generator.worker import LocalProcessWorker, ProcessJobRequest


def _request(tmp_path: Path, code: str, key: str, timeout: float = 2.0) -> ProcessJobRequest:
    return ProcessJobRequest([sys.executable, "-c", code], tmp_path, timeout, key)


def test_worker_reports_async_states_and_idempotent_submit(tmp_path) -> None:
    worker = LocalProcessWorker()
    request = _request(tmp_path, "import time; time.sleep(0.05)", "same")
    job_id = worker.submit(request)

    assert worker.submit(request) == job_id
    assert worker.get(job_id).status in {"queued", "running", "succeeded"}
    assert worker.wait(job_id).status == "succeeded"


def test_worker_classifies_failure_and_timeout(tmp_path) -> None:
    worker = LocalProcessWorker()
    with pytest.raises(PipelineError) as failed:
        worker.run(_request(tmp_path, "raise SystemExit(2)", "failure"))
    assert failed.value.code == ErrorCode.BACKEND_FAILED

    with pytest.raises(PipelineError) as timed_out:
        worker.run(_request(tmp_path, "import time; time.sleep(1)", "timeout", 0.01))
    assert timed_out.value.code == ErrorCode.BACKEND_TIMEOUT
    assert not list(tmp_path.rglob("*.tmp"))


def test_worker_can_cancel_running_job(tmp_path) -> None:
    worker = LocalProcessWorker()
    job_id = worker.submit(_request(tmp_path, "import time; time.sleep(5)", "cancel"))
    deadline = time.monotonic() + 1
    while worker.get(job_id).status == "queued" and time.monotonic() < deadline:
        time.sleep(0.005)

    job = worker.cancel(job_id)

    assert job.status == "cancelled"
    assert job.error_code == ErrorCode.CANCELLED.value

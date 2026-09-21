"""SAM3D upstream ownership in the same SQLite transaction domain as outer jobs."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .remote_protocol import RemoteJob, RemoteRequest
from .remote_service_store import RemoteServiceStore
from .remote_service_worker import ServiceOutput
from .serialization import canonical_json_bytes, sha256_bytes


class InvalidSam3DInput(ValueError):
    """A deterministic request validation failure before any network submission."""


class Sam3DServiceStore(RemoteServiceStore):
    """Missing per-job evidence is never re-created on the recovery path."""

    def initialize_bridge(self) -> None:
        with self._lock:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS sam3d_jobs "
                "(key TEXT PRIMARY KEY, intent BLOB NOT NULL, receipt BLOB, completion BLOB)"
            )

    def bridge_record(self, request: RemoteRequest) -> dict[str, Any]:
        with self._lock:
            if self.lookup(request) is None:
                raise ValueError("SAM3D owner missing")
            row = self.db.execute(
                "SELECT intent,receipt,completion FROM sam3d_jobs WHERE key=?",
                (request.submission_key,),
            ).fetchone()
            if row is None:
                raise ValueError("SAM3D ownership missing; recovery cannot recreate it")
            return {
                key: json.loads(value) if value is not None else None
                for key, value in zip(("intent", "receipt", "completion"), row, strict=True)
            }

    def claim_authorized(
        self, validate: Callable[[RemoteRequest], dict[str, Any]]
    ) -> RemoteRequest | None:
        """Claim and authorize in one transaction; invalid inputs become terminal."""
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                selected = None
                for key, raw in self.db.execute("SELECT key, job FROM jobs ORDER BY rowid"):
                    request = self.request_for(key)
                    if request is None:
                        raise ValueError("queued request disappeared")
                    job = RemoteJob.parse(json.loads(raw), request, expected_job_id=key)
                    if job.state == "running":
                        raise ValueError("queue blocked by unresolved running job: " + key)
                    if selected is None and job.state == "queued":
                        selected = request
                if selected is not None:
                    wire = self._wire(selected)
                    wire["state"] = "running"
                    # This intermediate state is never visible outside this transaction.
                    self.db.execute(
                        "UPDATE jobs SET job=? WHERE key=?",
                        (canonical_json_bytes(wire), selected.submission_key),
                    )
                    completion: dict[str, Any] | None
                    try:
                        intent = validate(selected)
                    except InvalidSam3DInput:
                        wire.update(
                            state="failed",
                            error={
                                "code": "SAM3D_INVALID_INPUT",
                                "detail": "SAM3D input validation failed before submission",
                            },
                        )
                        intent = {
                            "local_failure": "SAM3D_INVALID_INPUT",
                            "outer_request_digest": selected.request_digest,
                        }
                        completion = {"local_failure": wire["error"]}
                    else:
                        completion = None
                    self.db.execute(
                        "INSERT INTO sam3d_jobs (key,intent,completion) VALUES (?,?,?)",
                        (
                            selected.submission_key,
                            canonical_json_bytes(intent),
                            canonical_json_bytes(completion) if completion else None,
                        ),
                    )
                    RemoteJob.parse(wire, selected)
                    self.db.execute(
                        "UPDATE jobs SET job=? WHERE key=?",
                        (canonical_json_bytes(wire), selected.submission_key),
                    )
                self.db.execute("COMMIT")
                return selected
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def reject_submission(self, request: RemoteRequest, code: str) -> RemoteJob:
        """Pin an explicit nonacceptance and failure atomically; no receipt exists."""
        if code != "SAM3D_REJECTED":
            raise ValueError("invalid rejection code")
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                record = self.bridge_record(request)
                job = self.lookup(request)
                if (
                    job is None
                    or job.state != "running"
                    or record["receipt"] is not None
                    or record["completion"] is not None
                ):
                    raise ValueError("rejection requires unacknowledged running submission")
                error = {"code": code, "detail": "SAM3D explicitly rejected submission"}
                wire = self._wire(request)
                wire.update(state="failed", error=error)
                result = RemoteJob.parse(wire, request)
                self.db.execute(
                    "UPDATE sam3d_jobs SET completion=? WHERE key=?",
                    (canonical_json_bytes({"local_failure": error}), request.submission_key),
                )
                self.db.execute(
                    "UPDATE jobs SET job=? WHERE key=?",
                    (canonical_json_bytes(wire), request.submission_key),
                )
                self.db.execute("COMMIT")
                return result
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def fix_evidence(self, request: RemoteRequest, field: str, value: dict[str, Any]) -> None:
        if field not in {"receipt", "completion"}:
            raise ValueError("invalid SAM3D evidence field")
        body = canonical_json_bytes(value)
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                record = self.bridge_record(request)
                job = self.lookup(request)
                if job is None or job.state != "running":
                    raise ValueError("SAM3D evidence requires running job")
                old = record[field]
                if old is not None and canonical_json_bytes(old) != body:
                    raise ValueError("SAM3D fixed evidence changed")
                if field == "completion" and record["receipt"] is None:
                    raise ValueError("SAM3D completion requires fixed receipt")
                self.db.execute(
                    f"UPDATE sam3d_jobs SET {field}=? WHERE key=?", (body, request.submission_key)
                )
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def publish_outputs(
        self, request: RemoteRequest, outputs: dict[str, ServiceOutput]
    ) -> RemoteJob:
        """Commit all result bytes and success together; rollback never leaves partial evidence."""
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                job = self.lookup(request)
                if job is None or job.state != "running":
                    raise ValueError("SAM3D publication requires running job")
                record = self.bridge_record(request)
                if record["receipt"] is None or record["completion"] is None:
                    raise ValueError("SAM3D publication requires pinned terminal evidence")
                if record["completion"].get("status", {}).get("status") != "completed":
                    raise ValueError("SAM3D publication requires successful upstream result")
                if set(outputs) != {"mesh", "shape_metadata", "sam3d_evidence", "actual_mask"}:
                    raise ValueError("SAM3D publication requires complete result boundary")
                if sum(len(output.data) for output in outputs.values()) > 128 * 1024 * 1024:
                    raise ValueError("SAM3D outputs exceed aggregate limit")
                descriptors = []
                for name, output in outputs.items():
                    digest = sha256_bytes(output.data)
                    self.put_blob(output.data, digest)
                    descriptors.append(
                        {
                            "output_id": name,
                            "blob_digest": digest,
                            "byte_length": len(output.data),
                            "media_type": output.media_type,
                        }
                    )
                wire = self._wire(request)
                wire.update(state="succeeded", result={"outputs": descriptors})
                result = RemoteJob.parse(wire, request)
                self._validate_outputs(result)
                self.db.execute(
                    "UPDATE jobs SET job=? WHERE key=?",
                    (canonical_json_bytes(wire), request.submission_key),
                )
                self.db.execute("COMMIT")
                return result
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

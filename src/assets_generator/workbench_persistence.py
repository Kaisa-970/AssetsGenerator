"""Local durable workbench writes and injectable execution boundaries.

No launcher or Linux process probe is implemented here. The caller owns one service
lock for its entire lifetime. A commit failure poisons the writer until reopen:
replace may have succeeded even when the final directory fsync failed.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .artifact_store import LocalArtifactStore, _digest_path, create_manifest
from .models import ArtifactRef, BlobIdentity, BuildRun
from .serialization import canonical_json_bytes, read_json, sha256_bytes
from .workbench_models import (
    ChildRegistration,
    ProcessIdentity,
    ProcessObservation,
    decode_record,
    read_build_run,
)
from .workbench_state import Effect, Event, Transition, transition


class ProcessProbe(Protocol):
    def observe(self, identity: ProcessIdentity) -> ProcessObservation: ...


class LaunchChannel(Protocol):
    """A waiting launcher must exit without invoking its backend on close()."""

    def identify(self) -> ProcessIdentity: ...
    def release(self) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class CreationReceipt:
    idempotency_key: str
    request_digest: str
    run_id: str
    schema_version: str = "1.0"


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("invalid run/ownership identifier")
    return value


class DurableIO:
    """Each failpoint precedes an actual syscall. Filesystem must support directory fsync."""

    def __init__(self, failpoint: Callable[[str, Path], None] | None = None) -> None:
        self.failpoint = failpoint or (lambda operation, path: None)

    def sync_directory(self, path: Path) -> None:
        self.failpoint("directory_fsync", path)
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def mkdir(self, path: Path) -> None:
        if path.exists():
            return
        self.mkdir(path.parent)
        path.mkdir(exist_ok=True)
        self.sync_directory(path)
        self.sync_directory(path.parent)

    def sync_existing(self, path: Path) -> None:
        self.failpoint("file_fsync", path)
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
        # Also stabilize ancestors that may have been made by the legacy Store.
        for directory in (path.parent, *path.parent.parents):
            self.sync_directory(directory)

    def write(self, path: Path, data: bytes, *, exclusive: bool = False) -> None:
        self.mkdir(path.parent)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        self.failpoint("write", path)
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            self.failpoint("file_fsync", path)
            os.fsync(handle.fileno())
        try:
            self.failpoint("publish", path)
            if exclusive:
                # Link publishes fully-written bytes without overwriting another owner.
                os.link(temporary, path)
                temporary.unlink()
            else:
                os.replace(temporary, path)
            self.sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)


class WorkbenchRepository:
    def __init__(self, store: LocalArtifactStore, directory: Path, *, io: DurableIO | None = None):
        self.store = store
        self.directory = directory.absolute()
        self.io = io or DurableIO()
        self._lock_fd: int | None = None
        self._poisoned = False
        self._command_lock = threading.RLock()

    def __enter__(self) -> WorkbenchRepository:
        self.io.mkdir(self.directory)
        descriptor = os.open(self.directory / "service.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(descriptor)
            raise
        self._lock_fd = descriptor
        return self

    def __exit__(self, *args: object) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _ready(self) -> None:
        if self._lock_fd is None or self._poisoned:
            raise RuntimeError("writer needs an exclusive service lock and a healthy commit state")

    def _mutate(self, operation: Callable[[], Any]) -> Any:
        self._ready()
        try:
            return operation()
        except BaseException:
            self._poisoned = True
            raise

    def reserve_creation(self, receipt: CreationReceipt) -> CreationReceipt:
        with self._command_lock:
            return self._reserve_creation(receipt)

    def _reserve_creation(self, receipt: CreationReceipt) -> CreationReceipt:
        """The mapping is durable before the first parent BuildRun is created."""
        self._ready()
        _identifier(receipt.run_id)
        if not receipt.idempotency_key or not receipt.request_digest:
            raise ValueError("creation key/digest required")
        digest = sha256_bytes(receipt.idempotency_key.encode()).split(":")[1]
        path = self.directory / "requests" / f"{digest}.json"
        if path.exists():
            existing = decode_record(CreationReceipt, read_json(path))
            if existing.request_digest != receipt.request_digest:
                raise ValueError("creation idempotency key conflict")
            self._ensure_parent_owner(existing.run_id)
            return existing
        self._ensure_parent_owner(receipt.run_id)
        self._mutate(lambda: self.io.write(path, canonical_json_bytes(receipt), exclusive=True))
        return receipt

    def register_child(self, parent: BuildRun, registration: ChildRegistration) -> bool:
        with self._command_lock:
            return self._register_child(parent, registration)

    def _register_child(self, parent: BuildRun, registration: ChildRegistration) -> bool:
        """Return False for an existing identical reservation; never reset an existing run.

        Caller must establish/query the child index before emitting ChildRegistered.
        This method reloads the parent so an uncommitted in-memory receipt is insufficient.
        """
        self._ready()
        _identifier(registration.child_run_id)
        parent = self.load(parent.run_id)
        if parent.run_id != registration.parent_run_id:
            raise ValueError("registration parent mismatch")
        if parent.workbench is not None:
            attempt = parent.workbench.stage_states[registration.stage_id].current()
            receipt = attempt.command_receipt
            valid = (
                receipt is not None
                and receipt.child_run_id == registration.child_run_id
                and attempt.attempt == registration.attempt
                and attempt.input_digest == registration.input_digest
            )
        elif parent.dag is not None:
            dag_attempt = parent.dag.node_states[registration.stage_id].current()
            valid = (
                dag_attempt.child_reservation == registration
                and dag_attempt.attempt == registration.attempt
                and dag_attempt.input_digest == registration.input_digest
            )
        else:
            valid = False
        if not valid:
            raise ValueError("registration requires a matching durable parent receipt")
        path = self.store.root / "run_owners" / f"{registration.child_run_id}.json"
        if registration.registration_location != str(path):
            raise ValueError("registration location must match controlled ownership path")
        if (self.store.root / "parent_run_owners" / f"{registration.child_run_id}.json").exists():
            raise ValueError("child run ID belongs to a workbench parent")
        if path.exists():
            if decode_record(ChildRegistration, read_json(path)) != registration:
                raise ValueError("child run is owned by another attempt")
            return False
        if (self.store.root / "runs" / f"{registration.child_run_id}.json").exists():
            raise ValueError("cannot reserve an existing child run")
        self._mutate(
            lambda: self.io.write(path, canonical_json_bytes(registration), exclusive=True)
        )
        return True

    def load(self, run_id: str) -> BuildRun:
        _identifier(run_id)
        index = read_json(self.store.root / "runs" / f"{run_id}.json")
        reference = ArtifactRef(**index)
        if not self.store.verify_digest(reference):
            raise ValueError("run snapshot missing or corrupt")
        run = read_build_run(self.store.read_structured(reference))
        if run.run_id != run_id:
            raise ValueError("run index identity mismatch")
        return run

    def _sync_reference(self, reference: ArtifactRef, visited: set[str]) -> None:
        if reference.artifact_id in visited:
            return
        visited.add(reference.artifact_id)
        if not self.store.verify_digest(reference):
            raise ValueError(f"missing/corrupt dependency: {reference.artifact_id}")
        manifest = self.store.get_manifest(reference.artifact_id)
        blob = self.store.blob_path(reference)
        if manifest.identity.identity_metadata.get("media_type") == "application/json":
            value = json.loads(blob.read_bytes())
            for child in _references(value, schema_name=manifest.identity.schema_name):
                self._sync_reference(child, visited)
        self.io.sync_existing(blob)
        self.io.sync_existing(
            _digest_path(self.store.manifests_dir, reference.artifact_id).with_suffix(".json")
        )

    def _ensure_parent_owner(self, run_id: str) -> None:
        _identifier(run_id)
        path = self.store.root / "parent_run_owners" / f"{run_id}.json"
        expected = {
            "schema_version": "1.0",
            "run_id": run_id,
            "workbench_directory": str(self.directory.resolve()),
        }
        if (self.store.root / "run_owners" / f"{run_id}.json").exists():
            raise ValueError("parent run ID belongs to a child")
        if path.exists():
            if read_json(path) != expected:
                raise ValueError("parent run belongs to another workbench")
            self._mutate(lambda: self.io.sync_existing(path))
            return
        index = self.store.root / "runs" / f"{run_id}.json"
        if index.exists():
            raise ValueError("cannot claim an existing unowned parent run")
        self._mutate(lambda: self.io.write(path, canonical_json_bytes(expected), exclusive=True))

    def commit(self, run: BuildRun, *, owner: ChildRegistration | None = None) -> ArtifactRef:
        with self._command_lock:
            return self._commit(run, owner=owner)

    def _commit(self, run: BuildRun, *, owner: ChildRegistration | None = None) -> ArtifactRef:
        """Dependencies → snapshot blob → manifest → run index, all with file/dir fsync."""
        self._ready()
        _identifier(run.run_id)
        parent_owner = self.store.root / "parent_run_owners" / f"{run.run_id}.json"
        if (run.workbench is not None or run.dag is not None) and run.parent_run_id is None:
            if owner is not None:
                raise ValueError("parent run cannot have child ownership")
            self._ensure_parent_owner(run.run_id)
        elif parent_owner.exists():
            raise ValueError("owned parent requires orchestration state")
        ownership_path = self.store.root / "run_owners" / f"{run.run_id}.json"
        if ownership_path.exists():
            actual = decode_record(ChildRegistration, read_json(ownership_path))
            if owner != actual or run.parent_run_id != actual.parent_run_id:
                raise ValueError("reserved child run requires matching owner")
            self.io.sync_existing(ownership_path)
        elif owner is not None:
            raise ValueError("child ownership has not been reserved")

        def write_snapshot() -> ArtifactRef:
            registrations: list[ChildRegistration | None] = []
            if run.workbench is not None:
                registrations.extend(
                    attempt.child_registration
                    for stage in run.workbench.stage_states.values()
                    for attempt in stage.attempts
                )
            if run.dag is not None:
                registrations.extend(
                    attempt.child_registration
                    for node in run.dag.node_states.values()
                    for attempt in node.attempts
                )
            for registration in registrations:
                if registration is None:
                    continue
                owner_path = (
                    self.store.root
                    / "run_owners"
                    / f"{_identifier(registration.child_run_id)}.json"
                )
                if decode_record(ChildRegistration, read_json(owner_path)) != registration:
                    raise ValueError("child registration does not match durable ownership")
                self.io.sync_existing(owner_path)
                self._sync_registered_child(run, registration)
            visited: set[str] = set()
            raw = json.loads(canonical_json_bytes(run))
            for reference in _references(raw):
                self._sync_reference(reference, visited)
            data = canonical_json_bytes(run)
            blob = BlobIdentity(sha256_bytes(data), len(data))
            manifest = create_manifest(
                blob,
                kind="build_run",
                schema_name="BuildRun",
                schema_version="1.0",
                identity_metadata={"media_type": "application/json"},
            )
            reference = ArtifactRef(manifest.artifact_id)
            self.io.write(_digest_path(self.store.blobs_dir, blob.digest), data)
            self.io.write(
                _digest_path(self.store.manifests_dir, manifest.artifact_id).with_suffix(".json"),
                canonical_json_bytes(manifest),
            )
            self.io.write(
                self.store.root / "runs" / f"{run.run_id}.json", canonical_json_bytes(reference)
            )
            return reference

        result = self._mutate(write_snapshot)
        assert isinstance(result, ArtifactRef)
        return result

    def _sync_registered_child(self, run: BuildRun, registration: ChildRegistration) -> None:
        child = self.load(registration.child_run_id)
        if child.parent_run_id != run.run_id:
            raise ValueError("child index parent mismatch")
        child_index = self.store.root / "runs" / f"{registration.child_run_id}.json"
        child_ref = ArtifactRef(**read_json(child_index))
        self._sync_reference(child_ref, set())
        self.io.sync_existing(child_index)

    def apply(
        self,
        run_id: str,
        event: Event,
        execute: Callable[[Effect], None],
        *,
        channel: LaunchChannel | None = None,
    ) -> Transition:
        with self._command_lock:
            return self._apply(run_id, event, execute, channel=channel)

    def _apply(
        self,
        run_id: str,
        event: Event,
        execute: Callable[[Effect], None],
        *,
        channel: LaunchChannel | None = None,
    ) -> Transition:
        """Reload, transition, durable commit, effects. Never resend a duplicate launch."""
        self._ready()
        result = transition(self.load(run_id), event)
        if result.duplicate:
            return result
        try:
            self.commit(result.state)
        except BaseException:
            if channel is not None:
                channel.close()
            raise
        for effect in result.effects:
            execute(effect)
        return result


def _references(value: Any, *, schema_name: str | None = None) -> list[ArtifactRef]:
    # These fields contain Artifact identities, not Blob/ExecutionOutput/provenance IDs.
    fields = {
        "ProvenanceRecord": {"output_artifact_id": False, "derived_from_artifact_ids": True},
        "AssetDefinition": {"quality_report_ids": True},
    }
    extra: list[ArtifactRef] = []
    if isinstance(value, dict):
        for name, plural in fields.get(schema_name or "", {}).items():
            if name not in value:
                continue
            ids = value[name]
            if not plural:
                ids = [ids]
            if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
                raise ValueError(f"invalid artifact identity field: {name}")
            extra.extend(ArtifactRef(item) for item in ids)
    if isinstance(value, dict):
        if set(value) == {"artifact_id"} and isinstance(value["artifact_id"], str):
            return [ArtifactRef(value["artifact_id"])]
        return extra + [ref for item in value.values() for ref in _references(item)]
    if isinstance(value, list):
        return [ref for item in value for ref in _references(item)]
    return []

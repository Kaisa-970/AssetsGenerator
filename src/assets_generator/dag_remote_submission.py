"""Persist-before-network bridge for a remote request owned by a DAG attempt.

Internal foundation only: adapters and scheduling do not yet expose remote execution.
"""

from __future__ import annotations

from .dag_persistence import DagRepository
from .models import ArtifactRef, BuildRun, StructuredValue
from .remote_binding import RemoteAttemptBinding
from .remote_http import RemoteJobClient
from .remote_protocol import RemoteJob
from .remote_submission import RemoteSubmission
from .serialization import canonical_json_bytes, read_json, to_primitive


class DagRemoteSubmission:
    def __init__(self, repository: DagRepository, client: RemoteJobClient):
        self.repository = repository
        self.client = client
        self.journal = RemoteSubmission(repository, client)

    def _owner(self, binding: RemoteAttemptBinding, *, must_be_bound: bool) -> BuildRun:
        self.repository._ready()
        binding.__post_init__()
        if binding.endpoint != self.client.endpoint:
            raise ValueError("remote endpoint differs from fixed binding")
        owner = read_json(
            self.repository.store.root / "parent_run_owners" / f"{binding.run_id}.json"
        )
        if owner.get("workbench_directory") != str(self.repository.directory.resolve()):
            raise ValueError("remote parent belongs to another service directory")
        run = self.repository.load(binding.run_id)
        if run.dag is None or binding.node_id not in run.dag.node_states:
            raise ValueError("remote owner requires existing DAG node")
        node = run.dag.node_states[binding.node_id]
        node.__post_init__()
        attempt = node.current()
        if (
            attempt.attempt != binding.attempt
            or attempt.input_digest != binding.input_digest
            or attempt.binding_digest != binding.binding_digest
        ):
            raise ValueError("remote binding differs from current attempt")
        if attempt.remote_binding != binding and (
            must_be_bound or attempt.remote_binding is not None
        ):
            raise ValueError("remote request is not the persisted attempt binding")
        return run

    def prepare(self, binding: RemoteAttemptBinding, *, expected_revision: int) -> None:
        """Prepare journal, then fix ownership in BuildRun; neither operation sends."""
        with self.repository._command_lock:
            run = self._owner(binding, must_be_bound=False)
            assert run.dag is not None
            if type(expected_revision) is not int or expected_revision != run.dag.revision:
                raise ValueError("DAG revision conflict")
            node = run.dag.node_states[binding.node_id]
            attempt = node.current()
            if attempt.remote_binding is not None:
                self.journal._load(binding.request())  # Never recreate lost evidence.
                return
            if node.status != "running" or attempt.status != "running":
                raise ValueError("only a running attempt can prepare a remote request")
            self.journal.prepare(binding.request())
            attempt.remote_binding = binding
            self.repository.save(run, expected_revision=expected_revision)

    def submit(self, binding: RemoteAttemptBinding) -> RemoteJob:
        with self.repository._command_lock:
            run = self._owner(binding, must_be_bound=True)
            assert run.dag is not None
            node = run.dag.node_states[binding.node_id]
            if node.status != "running" or node.current().status != "running":
                raise ValueError("remote submit requires a running owner")
            self.journal._load(binding.request())
            return self.journal.submit(binding.request())

    def recover(self, binding: RemoteAttemptBinding) -> RemoteJob | None:
        with self.repository._command_lock:
            self._owner(binding, must_be_bound=True)
            return self.journal.recover(binding.request())

    def download(
        self, binding: RemoteAttemptBinding, output_id: str, *, max_bytes: int = 128 * 1024 * 1024
    ) -> bytes:
        with self.repository._command_lock:
            self._owner(binding, must_be_bound=True)
            return self.journal.download(binding.request(), output_id, max_bytes=max_bytes)

    def pinned_result(self, binding: RemoteAttemptBinding) -> RemoteJob:
        """Validate immutable terminal evidence locally, without contacting the service."""
        with self.repository._command_lock:
            run = self._owner(binding, must_be_bound=True)
            assert run.dag is not None
            reference = run.dag.node_states[binding.node_id].current().remote_result
            if reference is None:
                raise ValueError("remote terminal evidence is not pinned")
            self.repository.verify_reference_closure(reference)
            manifest = self.repository.store.get_manifest(reference.artifact_id)
            if (
                manifest.identity.kind != "remote_job_result"
                or manifest.identity.schema_name != "RemoteJobResult"
                or manifest.identity.schema_version != "1.0"
            ):
                raise ValueError("invalid remote terminal evidence contract")
            raw = self.repository.store.read_structured(reference)
            if set(raw) != {"binding", "job"} or canonical_json_bytes(
                raw["binding"]
            ) != canonical_json_bytes(binding):
                raise ValueError("remote terminal evidence owner mismatch")
            job = RemoteJob.parse(raw["job"], binding.request())
            if job.state not in {"succeeded", "failed"}:
                raise ValueError("remote terminal evidence requires a terminal job")
            return job

    def pin_result(self, binding: RemoteAttemptBinding, *, expected_revision: int) -> ArtifactRef:
        """Pin an already observed terminal job; never query or reconstruct pinned evidence."""
        with self.repository._command_lock:
            run = self._owner(binding, must_be_bound=True)
            assert run.dag is not None
            if type(expected_revision) is not int or expected_revision != run.dag.revision:
                raise ValueError("DAG revision conflict")
            attempt = run.dag.node_states[binding.node_id].current()
            if attempt.remote_result is not None:
                self.pinned_result(binding)
                return attempt.remote_result
            record = self.journal._load(binding.request())
            if record["phase"] != "observed":
                raise ValueError("remote terminal evidence requires an observed job")
            job = RemoteJob.parse(record["job"], binding.request())
            if job.state not in {"succeeded", "failed"}:
                raise ValueError("remote terminal evidence requires a terminal job")
            reference = self.repository.store.persist_structured(
                StructuredValue(
                    "remote_job_result",
                    "RemoteJobResult",
                    "1.0",
                    {"binding": to_primitive(binding), "job": record["job"]},
                )
            )
            attempt.remote_result = reference
            self.repository.save(run, expected_revision=expected_revision)
            return reference

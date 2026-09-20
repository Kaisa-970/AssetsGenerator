"""DAG snapshots use the existing durable Store commit and service ownership lock."""

from __future__ import annotations

import json

from .models import ArtifactRef, BuildRun
from .serialization import canonical_json_bytes
from .workbench_persistence import WorkbenchRepository, _references


class EvidenceError(ValueError):
    def __init__(self, artifact_id: str):
        self.artifact_id = artifact_id
        super().__init__(f"missing/corrupt dependency: {artifact_id}")


class DagRepository(WorkbenchRepository):
    """CAS writes; corrupt historical evidence can only be recorded as explicitly blocked.

    The exemption affects fsync of an already missing/corrupt historical artifact only.
    It never exempts the plan or permits a new corrupt dependency to enter a snapshot.
    """

    def verify_reference_closure(self, reference: ArtifactRef) -> None:
        visited: set[str] = set()

        def visit(ref: ArtifactRef) -> None:
            if ref.artifact_id in visited:
                return
            visited.add(ref.artifact_id)
            try:
                valid = self.store.verify_digest(ref)
            except (OSError, ValueError, KeyError):
                valid = False
            if not valid:
                raise EvidenceError(ref.artifact_id)
            for child in self._children(ref):
                visit(child)

        visit(reference)

    def _children(self, ref: ArtifactRef) -> list[ArtifactRef]:
        manifest = self.store.get_manifest(ref.artifact_id)
        if manifest.identity.identity_metadata.get("media_type") != "application/json":
            return []
        return _references(
            json.loads(self.store.blob_path(ref).read_bytes()),
            schema_name=manifest.identity.schema_name,
        )

    def create(self, run: BuildRun) -> ArtifactRef:
        with self._command_lock:
            self._ready()
            if run.dag is None or run.workbench is not None or run.inputs:
                raise ValueError("DAG state required without workbench state")
            if run.dag.revision != 0 or run.dag.invalid_evidence:
                raise ValueError("new DAG run requires revision zero and valid evidence")
            if (self.store.root / "runs" / f"{run.run_id}.json").exists():
                raise ValueError("run already exists")
            return self.commit(run)

    def save(self, run: BuildRun, *, expected_revision: int) -> ArtifactRef:
        with self._command_lock:
            self._ready()
            previous = self.load(run.run_id)
            if run.dag is None or previous.dag is None or run.workbench is not None or run.inputs:
                raise ValueError("DAG state required")
            if previous.dag.revision != expected_revision or run.dag.revision != expected_revision:
                raise ValueError("DAG revision conflict")
            if canonical_json_bytes(
                (run.dag.plan, run.dag.plan_id, run.dag.named_actual_inputs)
            ) != canonical_json_bytes(
                (previous.dag.plan, previous.dag.plan_id, previous.dag.named_actual_inputs)
            ):
                raise ValueError("DAG plan and actual inputs are immutable")
            if set(run.dag.node_states) != set(previous.dag.node_states):
                raise ValueError("DAG node instances are immutable")
            for key, old_node in previous.dag.node_states.items():
                new_node = run.dag.node_states[key]
                if len(new_node.attempts) < len(old_node.attempts):
                    raise ValueError("DAG attempt history cannot be removed")
                if len(new_node.attempts) > len(old_node.attempts):
                    if canonical_json_bytes(
                        new_node.attempts[: len(old_node.attempts)]
                    ) != canonical_json_bytes(old_node.attempts):
                        raise ValueError("historical DAG attempts are immutable")
                # Only the active last attempt may be completed or interrupted.
                if canonical_json_bytes(
                    new_node.attempts[: max(0, len(old_node.attempts) - 1)]
                ) != canonical_json_bytes(old_node.attempts[:-1]):
                    raise ValueError("historical DAG attempts are immutable")
                if old_node.attempts and old_node.current().status == "succeeded":
                    if canonical_json_bytes(
                        new_node.attempts[len(old_node.attempts) - 1]
                    ) != canonical_json_bytes(old_node.current()):
                        raise ValueError("successful DAG attempt evidence is immutable")
                new_node.__post_init__()
            run.dag.__post_init__()
            exempt = set(run.dag.invalid_evidence)
            if exempt:
                if exempt - set(previous.dag.invalid_evidence) and not any(
                    node.recovery_blocked_reason for node in run.dag.node_states.values()
                ):
                    raise ValueError("invalid evidence requires an explicit recovery block")
                if any(not reason for reason in run.dag.invalid_evidence.values()):
                    raise ValueError("invalid evidence requires a reason")
                # Walk old evidence, stopping at corrupt objects. A fresh bad reference
                # cannot be smuggled into this history-only exemption.
                reachable: set[str] = set()
                pending = _references(json.loads(canonical_json_bytes(previous)))
                while pending:
                    ref = pending.pop()
                    if ref.artifact_id in reachable:
                        continue
                    reachable.add(ref.artifact_id)
                    try:
                        if self.store.verify_digest(ref):
                            pending.extend(self._children(ref))
                    except (OSError, ValueError, KeyError):
                        pass
                if not exempt <= reachable:
                    raise ValueError("invalid evidence must belong to previous snapshot")
                if run.dag.plan.artifact_id in exempt:
                    raise ValueError("cannot exempt the execution plan")
            # Previously recorded bad history can remain after successful retry. It
            # cannot be used as live evidence by an unblocked active node.
            if exempt:
                for node in run.dag.node_states.values():
                    if node.recovery_blocked_reason or not node.attempts:
                        continue
                    if node.status not in {
                        "running",
                        "succeeded",
                        "waiting_for_input",
                    }:
                        continue
                    for ref in _references(json.loads(canonical_json_bytes(node.current()))):
                        self.verify_reference_closure(ref)
            self._dag_exemptions = exempt
            run.dag.revision = expected_revision + 1
            try:
                result = self.commit(run)
            except BaseException:
                run.dag.revision = expected_revision
                raise
            finally:
                self._dag_exemptions = set()
            return result

    def _sync_reference(self, reference: ArtifactRef, visited: set[str]) -> None:
        if reference.artifact_id in getattr(self, "_dag_exemptions", set()):
            try:
                valid = self.store.verify_digest(reference)
            except (OSError, ValueError, KeyError):
                valid = False
            if not valid:
                visited.add(reference.artifact_id)
                return
        super()._sync_reference(reference, visited)

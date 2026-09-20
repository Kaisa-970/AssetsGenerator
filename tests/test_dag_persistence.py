from __future__ import annotations

import json
from pathlib import Path

import pytest

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_models import DagAttempt, DagNodeState, DagState
from assets_generator.dag_persistence import DagRepository, EvidenceError
from assets_generator.models import ArtifactRef, BuildRun, StructuredValue
from assets_generator.serialization import canonical_json_bytes
from assets_generator.workbench_models import read_build_run


def evidence(store: LocalArtifactStore, value: dict[str, object]) -> ArtifactRef:
    return store.persist_structured(StructuredValue("dag_plan", "TestPlan", "1.0", value))


def run_record(store: LocalArtifactStore) -> BuildRun:
    ref = evidence(store, {"test": 1})
    return BuildRun(
        "dag_test",
        "diamond",
        "1",
        "pending",
        {},
        [],
        "now",
        None,
        dag=DagState(ref, "plan-id", {"items": [ref, ref]}, {"a": DagNodeState("a")}),
    )


def test_legacy_and_typed_roundtrip(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    run = run_record(store)
    assert run.dag is not None
    ref = run.dag.plan
    run.dag.node_states["a"].attempts.append(
        DagAttempt(
            1,
            "waiting_for_input",
            {"items": [ref, ref]},
            "digest",
            request=ref,
            decision=ref,
            provenance={"out": [ref]},
        )
    )
    run.dag.receipts["command"] = {"decision_ref": {"artifact_id": ref.artifact_id}}
    with DagRepository(store, tmp_path / "service") as repo:
        repo.create(run)
        assert repo.load(run.run_id) == run
        loaded = repo.load(run.run_id)
        assert loaded.dag is not None
        assert loaded.dag.node_states["a"].current().request == ref
    run.dag = None
    raw = json.loads(canonical_json_bytes(run))
    assert "dag" not in raw
    assert read_build_run(raw) == run


def test_revision_lock_and_poison(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    directory = tmp_path / "service"
    with DagRepository(store, directory) as repo:
        run = run_record(store)
        repo.create(run)
        with pytest.raises(BlockingIOError):
            with DagRepository(store, directory):
                pass
        repo.save(run, expected_revision=0)
        with pytest.raises(ValueError, match="revision conflict"):
            repo.save(run, expected_revision=0)

        # A validation error does not poison the writer; uncertain I/O does.
        def fail(operation: str, path: Path) -> None:
            if operation == "publish":
                raise OSError("injected failure")

        repo.io.failpoint = fail
        with pytest.raises(OSError, match="injected"):
            repo.save(run, expected_revision=1)
        with pytest.raises(RuntimeError, match="healthy commit"):
            repo.save(run, expected_revision=1)
    with DagRepository(store, directory) as repo:
        assert repo.load(run.run_id).dag.revision == 1


def test_corrupt_history_can_be_durably_blocked(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    with DagRepository(store, tmp_path / "service") as repo:
        run = run_record(store)
        assert run.dag is not None
        output = evidence(store, {"result": 2})
        parent = evidence(store, {"output": {"artifact_id": output.artifact_id}})
        node = run.dag.node_states["a"]
        node.attempts.append(DagAttempt(1, "succeeded", outputs={"out": parent}))
        repo.create(run)
        store.blob_path(output).write_bytes(b"corrupt")
        with pytest.raises(EvidenceError) as error:
            repo.verify_reference_closure(parent)
        assert error.value.artifact_id == output.artifact_id
        run.dag.invalid_evidence[output.artifact_id] = "digest mismatch"
        with pytest.raises(ValueError, match="explicit recovery block"):
            repo.save(run, expected_revision=0)
        node.recovery_blocked_reason = "output corrupted"
        node.status = "recovery_blocked"
        run.status = "recovery_blocked"
        repo.save(run, expected_revision=0)
        restored = repo.load(run.run_id)
        assert restored.dag.node_states["a"].current().status == "succeeded"
        assert restored.dag.invalid_evidence == {output.artifact_id: "digest mismatch"}
        unknown = "sha256:" + "0" * 64
        run.dag.invalid_evidence[unknown] = "new bad reference"
        with pytest.raises(ValueError, match="previous snapshot"):
            repo.save(run, expected_revision=1)


def test_retry_preserves_bad_history_without_permanent_block(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    with DagRepository(store, tmp_path / "service") as repo:
        run = run_record(store)
        assert run.dag is not None
        output = evidence(store, {"old": True})
        node = run.dag.node_states["a"]
        node.attempts.append(DagAttempt(1, "succeeded", outputs={"out": output}))
        node.status = "succeeded"
        repo.create(run)
        store.blob_path(output).write_bytes(b"bad")
        run.dag.invalid_evidence[output.artifact_id] = "corrupt historical output"
        node.recovery_blocked_reason = "corrupt output"
        node.status = "recovery_blocked"
        repo.save(run, expected_revision=0)
        node.recovery_blocked_reason = None
        node.status = "succeeded"
        with pytest.raises(EvidenceError):
            repo.save(run, expected_revision=1)
        replacement = evidence(store, {"new": True})
        node.attempts.append(DagAttempt(2, "succeeded", outputs={"out": replacement}))
        repo.save(run, expected_revision=1)
        loaded = repo.load(run.run_id)
        assert loaded.dag.node_states["a"].recovery_blocked_reason is None
        assert loaded.dag.node_states["a"].attempts[0].outputs == {"out": output}
        assert loaded.dag.invalid_evidence == run.dag.invalid_evidence


def test_rejects_history_and_state_identity_changes(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    with DagRepository(store, tmp_path / "service") as repo:
        run = run_record(store)
        assert run.dag is not None
        run.dag.node_states["a"].attempts.append(DagAttempt(1, "succeeded"))
        repo.create(run)
        run.dag.node_states["a"].current().input_digest = "rewritten"
        with pytest.raises(ValueError, match="successful DAG attempt"):
            repo.save(run, expected_revision=0)
        run = repo.load(run.run_id)
        run.dag.node_states["b"] = DagNodeState("b")
        with pytest.raises(ValueError, match="node instances"):
            repo.save(run, expected_revision=0)
        raw = json.loads(canonical_json_bytes(repo.load(run.run_id)))
        raw["dag"]["node_states"]["a"]["node_id"] = "other"
        with pytest.raises(ValueError):
            read_build_run(raw)
        raw = json.loads(canonical_json_bytes(repo.load(run.run_id)))
        raw["workbench"] = {}
        with pytest.raises(ValueError, match="both workbench and dag"):
            read_build_run(raw)


def test_input_identity_distinguishes_boolean_from_integer(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    with DagRepository(store, tmp_path / "service") as repo:
        run = run_record(store)
        run.dag.named_actual_inputs["number"] = StructuredValue("test", "Test", "1", {"x": 1})
        repo.create(run)
        run.dag.named_actual_inputs["number"] = StructuredValue("test", "Test", "1", {"x": True})
        with pytest.raises(ValueError, match="actual inputs are immutable"):
            repo.save(run, expected_revision=0)


def test_waiting_request_cannot_reuse_corrupt_history_exemption(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    with DagRepository(store, tmp_path / "service") as repo:
        run = run_record(store)
        old = evidence(store, {"request": "old"})
        node = run.dag.node_states["a"]
        node.attempts.append(DagAttempt(1, "failed", request=old))
        repo.create(run)
        store.blob_path(old).write_bytes(b"corrupt")
        node.recovery_blocked_reason = "invalid prior request"
        node.status = "recovery_blocked"
        run.dag.invalid_evidence[old.artifact_id] = "invalid prior request"
        repo.save(run, expected_revision=0)
        node.recovery_blocked_reason = None
        node.status = "waiting_for_input"
        node.attempts.append(DagAttempt(2, "waiting_for_input", request=old))
        with pytest.raises(EvidenceError) as error:
            repo.save(run, expected_revision=1)
        assert error.value.artifact_id == old.artifact_id
        # Validation failure precedes the commit boundary and leaves writer healthy.
        node.attempts[-1].request = evidence(store, {"request": "new"})
        repo.save(run, expected_revision=1)


@pytest.mark.parametrize(
    "status", ["running", "waiting_for_input", "succeeded", "failed", "interrupted"]
)
def test_current_attempt_must_match_node_status(status: str) -> None:
    with pytest.raises(ValueError, match="must match"):
        DagNodeState("a", status)
    other = "failed" if status != "failed" else "succeeded"
    with pytest.raises(ValueError, match="must match"):
        DagNodeState("a", status, [DagAttempt(1, other)])
    assert DagNodeState("a", status, [DagAttempt(1, status)]).status == status


def test_recovery_blocked_requires_a_reason() -> None:
    with pytest.raises(ValueError, match="requires a reason"):
        DagNodeState("a", "recovery_blocked")
    with pytest.raises(ValueError, match="requires a reason"):
        DagNodeState("a", "recovery_blocked", recovery_blocked_reason="  ")
    assert (
        DagNodeState(
            "a", "recovery_blocked", recovery_blocked_reason="output digest mismatch"
        ).status
        == "recovery_blocked"
    )


def test_recovery_reason_cannot_leak_into_other_states() -> None:
    with pytest.raises(ValueError, match="only valid"):
        DagNodeState("a", recovery_blocked_reason="stale evidence")


def test_dependency_blocked_node_has_no_execution_attempt() -> None:
    with pytest.raises(ValueError, match="cannot contain a running attempt"):
        DagNodeState("a", "blocked", [DagAttempt(1, "running")])
    for status in ("failed", "interrupted", "succeeded", "waiting_for_input"):
        assert DagNodeState("a", "blocked", [DagAttempt(1, status)]).status == "blocked"


@pytest.mark.parametrize("status", ["failed", "interrupted"])
def test_retry_cannot_rewrite_previous_terminal_attempt(tmp_path: Path, status: str) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    with DagRepository(store, tmp_path / "service") as repo:
        run = run_record(store)
        node = run.dag.node_states["a"]
        node.status = status
        node.attempts.append(DagAttempt(1, status, error_detail="original"))
        repo.create(run)
        node.current().error_detail = "rewritten"
        node.attempts.append(DagAttempt(2, "running"))
        node.status = "running"
        with pytest.raises(ValueError, match="historical DAG attempts"):
            repo.save(run, expected_revision=0)

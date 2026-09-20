"""Independent multi-view recovery and child failure audit regressions."""

from dataclasses import replace

import pytest
from test_dag_multi_view import setup

from assets_generator.contracts import ContractError
from assets_generator.dag_adapters import NodeExecutionContext
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.workbench_context import ChildRunContext


def context_for(repo, plan, run, node_id, **changes):
    attempt = run.dag.node_states[node_id].current()
    context = NodeExecutionContext(
        run.run_id,
        node_id,
        attempt.resolved_inputs,
        plan.bindings[node_id].parameters,
        repo.store,
        attempt_id=f"{run.run_id}/{node_id}/{attempt.attempt}",
        input_digest=attempt.input_digest,
        child_context=ChildRunContext(repo, attempt.child_registration),
        output_path=repo.directory
        / "executions"
        / run.run_id
        / attempt.child_registration.child_run_id
        / "release",
    )
    return replace(context, **changes)


def test_geometry_recovery_rejects_outputs_not_matching_fixed_evidence(tmp_path, monkeypatch):
    store, observations, registry, relations, plan, calls = setup(tmp_path, monkeypatch)
    with DagRepository(store, tmp_path / "service") as repo:
        engine = DagEngine(repo, registry, relations)
        run = engine.create(plan, {"observations": observations})
        save = engine._save

        def crash(current):
            if current.dag.node_states["geometry"].status == "succeeded":
                raise KeyboardInterrupt("parent not persisted")
            save(current)

        monkeypatch.setattr(engine, "_save", crash)
        with pytest.raises(KeyboardInterrupt):
            engine.drain(run.run_id)
        run = repo.load(run.run_id)
        attempt = run.dag.node_states["geometry"].current()
        child = repo.load(attempt.child_registration.child_run_id)
        points = child.node_attempts[0].outputs["points"]
        identity = store.get_manifest(points.artifact_id).identity
        other = store.persist_bytes(
            b"different frontend points",
            kind=identity.kind,
            schema_name=identity.schema_name,
            schema_version=identity.schema_version,
            identity_metadata=identity.identity_metadata,
        )
        child.node_attempts[0].outputs["points"] = other
        repo.commit(child, owner=attempt.child_registration)
        restored = DagEngine(repo, registry, relations).recover(run.run_id)
        state = restored.dag.node_states["geometry"]
        assert state.status == "failed"
        assert "fixed evidence" in state.current().error_detail
        assert calls == {"geometry": 1, "reconstruction": 0}


def test_release_recovery_checks_requested_appearance(tmp_path, monkeypatch):
    store, observations, registry, relations, plan, calls = setup(tmp_path, monkeypatch)
    with DagRepository(store, tmp_path / "service") as repo:
        engine = DagEngine(repo, registry, relations)
        run = engine.drain(engine.create(plan, {"observations": observations}).run_id)
        assert run.status == "succeeded"
        adapter = registry.resolve(plan.bindings["release"])
        context = context_for(repo, plan, run, "release")
        assert adapter.recover(context).outputs == run.dag.node_states["release"].current().outputs
        other = (
            "apply_material"
            if context.parameters["appearance_mode"] == "preserve_mesh"
            else "preserve_mesh"
        )
        with pytest.raises(ContractError, match="appearance"):
            adapter.recover(
                replace(context, parameters={**context.parameters, "appearance_mode": other})
            )
        assert calls == {"geometry": 1, "reconstruction": 1}


@pytest.mark.parametrize("node_id", ["geometry", "release"])
def test_profile_failure_records_terminal_owned_child(tmp_path, monkeypatch, node_id):
    store, observations, registry, relations, plan, calls = setup(tmp_path, monkeypatch)
    adapter = registry.resolve(plan.bindings[node_id])

    def fail_profile():
        raise ContractError("backend environment changed before execution")

    adapter.profile = replace(adapter.profile, check=fail_profile)
    with DagRepository(store, tmp_path / "service") as repo:
        engine = DagEngine(repo, registry, relations)
        run = engine.drain(engine.create(plan, {"observations": observations}).run_id)
        state = run.dag.node_states[node_id]
        assert state.status == "failed"
        attempt = state.current()
        assert "environment changed" in attempt.error_detail
        assert attempt.child_registration is not None
        child = repo.load(attempt.child_registration.child_run_id)
        assert child.status == "failed"
        assert child.finished_at is not None
        assert child.node_attempts and child.node_attempts[-1].status == "failed"
        assert child.node_attempts[-1].error_code == "contract_error"
        assert calls == (
            {"geometry": 0, "reconstruction": 0}
            if node_id == "geometry"
            else {"geometry": 1, "reconstruction": 1}
        )

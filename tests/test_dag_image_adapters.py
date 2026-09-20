from dataclasses import replace
from pathlib import Path

import pytest
from test_workbench_engine import fixture_engine

from assets_generator.contracts import ContractError
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_image_adapters import (
    DagImageBuildAdapter,
    DagMaskSelectionAdapter,
    DagProposalAdapter,
)
from assets_generator.dag_persistence import DagRepository
from assets_generator.models import ArtifactRef
from assets_generator.pipeline import compile_pipeline, load_default_operator_specs, load_pipeline


def image_plan(profile):
    registry = AdapterRegistry()
    for adapter in (
        DagProposalAdapter(profile),
        DagMaskSelectionAdapter(),
        DagImageBuildAdapter(profile),
    ):
        registry.register(adapter)
    compiled = compile_pipeline(
        load_pipeline(Path("examples/dag-image-asset.yaml")),
        load_default_operator_specs(),
        require_explicit_joins=True,
    )
    return registry, registry.bind_plan(compiled)


def test_image_yaml_wait_decide_publish_and_recover(tmp_path):
    store, image, profile = fixture_engine(tmp_path)
    registry, plan = image_plan(profile)
    with DagRepository(store, tmp_path / "dag") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"image": image}).run_id)
        assert run.status == "waiting_for_input"
        human = run.dag.node_states["choose_object"].current()
        request = store.read_structured(human.request)
        assert request["decision_contract"] == "single-proposal-mask-edit@1"
        assert human.child_registration is None
        waiting_id = run.run_id
    with DagRepository(store, tmp_path / "dag") as repo:
        engine = DagEngine(repo, registry)
        run = engine.recover(waiting_id)
        assert run.status == "waiting_for_input"
        run = engine.decide(
            run.run_id,
            "choose_object",
            expected_revision=run.dag.revision,
            idempotency_key="mask1",
            reviewer="CPU fixture",
            payload={"proposal_id": "p0", "invert": False, "keep_largest": True},
        )
        assert run.status == "succeeded", run
        for state in run.dag.node_states.values():
            assert len(state.attempts) == 1
            child = repo.load(state.current().child_registration.child_run_id)
            assert child.status == "succeeded"
            assert child.parent_run_id == run.run_id
        generated = run.dag.node_states["generate_asset"].current()
        child = repo.load(generated.child_registration.child_run_id)
        assert "selection_binding" in child.inputs
        assert "import_binding" in child.inputs
        assert store.verify_digest(generated.outputs["glb"])
        assert engine.recover(run.run_id).status == "succeeded"


def test_profile_identity_is_bound_and_cannot_be_overridden(tmp_path):
    _, _, profile = fixture_engine(tmp_path)
    adapter = DagProposalAdapter(profile)
    first = adapter.spec.defaults["profile_digest"]
    changed = DagProposalAdapter(replace(profile, proposal_identity={"model": "other"}))
    assert first != changed.spec.defaults["profile_digest"]
    with pytest.raises(ContractError, match="enum"):
        adapter.spec.normalize_parameters({"profile_digest": "unverified"})


def test_adapters_recover_published_child_without_executing_models(tmp_path, monkeypatch):
    from assets_generator.dag_adapters import NodeExecutionContext
    from assets_generator.workbench_context import ChildRunContext

    store, image, profile = fixture_engine(tmp_path)
    registry, plan = image_plan(profile)
    with DagRepository(store, tmp_path / "dag") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"image": image}).run_id)
        run = engine.decide(
            run.run_id,
            "choose_object",
            expected_revision=run.dag.revision,
            idempotency_key="mask1",
            reviewer="CPU fixture",
            payload={"proposal_id": "p0", "invert": False, "keep_largest": True},
        )
        assert run.status == "succeeded"
        from assets_generator.artifact_store import LocalArtifactStore

        monkeypatch.setattr(
            LocalArtifactStore,
            "transaction",
            lambda self: pytest.fail("adapter recovery must not publish artifacts"),
        )
        for node, adapter in [
            ("candidates", DagProposalAdapter(profile)),
            ("choose_object", DagMaskSelectionAdapter()),
            ("generate_asset", DagImageBuildAdapter(profile)),
        ]:
            attempt = run.dag.node_states[node].current()
            context = NodeExecutionContext(
                run.run_id,
                node,
                attempt.resolved_inputs,
                plan.bindings[node].parameters,
                store,
                decision=attempt.decision,
                worker=object(),
                child_context=ChildRunContext(repo, attempt.child_registration),
                output_path=repo.directory
                / "executions"
                / run.run_id
                / attempt.child_registration.child_run_id
                / "release",
            )
            restored = adapter.recover(context)
            assert restored.outputs == attempt.outputs


def test_child_publish_survives_parent_save_crash_without_model_rerun(tmp_path, monkeypatch):
    store, image, profile = fixture_engine(tmp_path)
    registry, plan = image_plan(profile)
    with DagRepository(store, tmp_path / "dag") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"image": image}).run_id)
        original_save = engine._save

        def crash_after_child(current):
            if current.dag.node_states["generate_asset"].status == "succeeded":
                raise KeyboardInterrupt("parent save lost after child publication")
            return original_save(current)

        monkeypatch.setattr(engine, "_save", crash_after_child)
        with pytest.raises(KeyboardInterrupt):
            engine.decide(
                run.run_id,
                "choose_object",
                expected_revision=run.dag.revision,
                idempotency_key="confirmed",
                reviewer="CPU fixture",
                payload={"proposal_id": "p0", "invert": False, "keep_largest": True},
            )
        durable = repo.load(run.run_id)
        attempt = durable.dag.node_states["generate_asset"].current()
        assert attempt.status == "running"
        child = repo.load(attempt.child_registration.child_run_id)
        assert child.status == "succeeded"
        import assets_generator.dag_image_adapters as adapters

        monkeypatch.setattr(
            adapters,
            "build_image_asset",
            lambda **kwargs: pytest.fail("model must not run during recovery"),
        )
        restored = DagEngine(repo, registry).recover(run.run_id)
        assert restored.status == "succeeded"
        assert len(restored.dag.node_states["generate_asset"].attempts) == 1
        assert (
            restored.dag.node_states["generate_asset"].current().child_registration
            == attempt.child_registration
        )


@pytest.mark.parametrize("node_id", ["choose_object", "generate_asset"])
def test_missing_child_evidence_is_not_recreated_after_parent_crash(tmp_path, monkeypatch, node_id):
    store, image, profile = fixture_engine(tmp_path)
    registry, plan = image_plan(profile)
    with DagRepository(store, tmp_path / "dag") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"image": image}).run_id)
        save = engine._save

        def crash(current):
            if current.dag.node_states[node_id].status == "succeeded":
                raise KeyboardInterrupt("parent snapshot lost")
            save(current)

        monkeypatch.setattr(engine, "_save", crash)
        with pytest.raises(KeyboardInterrupt):
            engine.decide(
                run.run_id,
                "choose_object",
                expected_revision=run.dag.revision,
                idempotency_key="crash",
                reviewer="fixture",
                payload={"proposal_id": "p0", "invert": True, "keep_largest": True},
            )
        durable = repo.load(run.run_id)
        attempt = durable.dag.node_states[node_id].current()
        child = repo.load(attempt.child_registration.child_run_id)
        if node_id == "choose_object":
            selection = store.read_structured(child.node_attempts[0].outputs["selection"])
            proposals = store.read_structured(ArtifactRef(**selection["proposals"]))
            ref = ArtifactRef(
                **next(p for p in proposals["proposals"] if p["proposal_id"] == "p0")["mask"]
            )
        else:
            ref = child.inputs["import_binding"]
        path = store.blob_path(ref)
        path.unlink()
        restored = DagEngine(repo, registry).recover(run.run_id)
        assert restored.status == "recovery_blocked"
        assert not path.exists()
        assert ref.artifact_id in repo.load(run.run_id).dag.invalid_evidence
        assert DagEngine(repo, registry).recover(run.run_id).status == "recovery_blocked"
        assert not path.exists()

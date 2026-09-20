from pathlib import Path

import pytest
from test_multi_view_workflow import (
    ContractGeometryFrontend,
    ContractReconstruction,
    _observations,
    _plan,
)

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_multi_view import (
    GeometryAdapter,
    MultiViewProfile,
    ReconstructionAdapter,
    ReleaseAdapter,
)
from assets_generator.dag_persistence import DagRepository
from assets_generator.multi_view_relations import register_multi_view_relations
from assets_generator.pipeline import compile_pipeline, load_operator_specs, load_pipeline
from assets_generator.relations import default_relation_registry


def setup(tmp_path, monkeypatch):
    calls = {"geometry": 0, "reconstruction": 0}
    g = ContractGeometryFrontend.estimate
    r = ContractReconstruction.reconstruct

    def geometry(self, *args):
        calls["geometry"] += 1
        return g(self, *args)

    def reconstruction(self, *args):
        calls["reconstruction"] += 1
        return r(self, *args)

    monkeypatch.setattr(ContractGeometryFrontend, "estimate", geometry)
    monkeypatch.setattr(ContractReconstruction, "reconstruct", reconstruction)
    store = LocalArtifactStore(tmp_path / "store")
    observations = _observations(tmp_path, store)
    profile = MultiViewProfile(
        _plan(ContractGeometryFrontend(), ContractReconstruction()),
        {"fixture": "1"},
        test_only=True,
    )
    registry = AdapterRegistry()
    for adapter in (
        GeometryAdapter(profile),
        ReconstructionAdapter(profile),
        ReleaseAdapter(profile),
    ):
        registry.register(adapter)
    relations = default_relation_registry()
    register_multi_view_relations(relations)
    plan = registry.bind_plan(
        compile_pipeline(
            load_pipeline(Path("examples/dag-multi-view-asset.yaml")),
            load_operator_specs(Path("examples/dag-multi-view-operators.yaml")),
            relation_registry=relations,
            require_explicit_joins=True,
        ),
        relation_registry=relations,
    )
    return store, observations, registry, relations, plan, calls


def test_real_graph_shape_release_and_recovery(tmp_path, monkeypatch):
    store, observations, registry, relations, plan, calls = setup(tmp_path, monkeypatch)
    with DagRepository(store, tmp_path / "service") as repo:
        engine = DagEngine(repo, registry, relations)
        run = engine.drain(engine.create(plan, {"observations": observations}).run_id)
        assert run.status == "succeeded", [
            (n.node_id, n.current().error_detail)
            for n in run.dag.node_states.values()
            if n.attempts
        ]
        assert calls == {"geometry": 1, "reconstruction": 1}
        release = run.dag.node_states["release"].current()
        child = repo.load(release.child_registration.child_run_id)
        assert (
            child.inputs["prepared_geometry"]
            == run.dag.node_states["geometry"].current().outputs["evidence"]
        )
        assert [a.execution_mode for a in child.node_attempts[:2]] == [
            "reused_input",
            "reused_input",
        ]
        for ref in release.outputs.values():
            repo.verify_reference_closure(ref)
        assert engine.recover(run.run_id).status == "succeeded"
        assert calls == {"geometry": 1, "reconstruction": 1}


@pytest.mark.parametrize("node_id", ["geometry", "reconstruction", "release"])
def test_child_completed_before_parent_crash_recovers_without_inference(
    tmp_path, monkeypatch, node_id
):
    store, observations, registry, relations, plan, calls = setup(tmp_path, monkeypatch)
    with DagRepository(store, tmp_path / "service") as repo:
        engine = DagEngine(repo, registry, relations)
        run = engine.create(plan, {"observations": observations})
        save = engine._save

        def crash(current):
            if current.dag.node_states[node_id].status == "succeeded":
                raise KeyboardInterrupt("parent snapshot lost")
            save(current)

        monkeypatch.setattr(engine, "_save", crash)
        with pytest.raises(KeyboardInterrupt):
            engine.drain(run.run_id)
        recovered = DagEngine(repo, registry, relations).drain(run.run_id)
        assert recovered.status == "succeeded", [
            (n.node_id, n.recovery_blocked_reason) for n in recovered.dag.node_states.values()
        ]
        assert calls == {"geometry": 1, "reconstruction": 1}
        assert all(len(n.attempts) == 1 for n in recovered.dag.node_states.values())


def test_release_failure_retry_keeps_models(tmp_path, monkeypatch):
    import assets_generator.dag_multi_view as adapters

    store, observations, registry, relations, plan, calls = setup(tmp_path, monkeypatch)
    original = adapters.build_multi_view_asset

    def fail(**kwargs):
        raise ValueError("release injected failure")

    monkeypatch.setattr(adapters, "build_multi_view_asset", fail)
    with DagRepository(store, tmp_path / "service") as repo:
        engine = DagEngine(repo, registry, relations)
        run = engine.drain(engine.create(plan, {"observations": observations}).run_id)
        assert run.status == "failed"
        monkeypatch.setattr(adapters, "build_multi_view_asset", original)
        run = engine.retry(run.run_id, "release", run.dag.revision)
        assert run.status == "succeeded"
        assert calls == {"geometry": 1, "reconstruction": 1}
        assert len(run.dag.node_states["release"].attempts) == 2


def test_missing_geometry_evidence_blocks_without_repair(tmp_path, monkeypatch):
    store, observations, registry, relations, plan, calls = setup(tmp_path, monkeypatch)
    with DagRepository(store, tmp_path / "service") as repo:
        engine = DagEngine(repo, registry, relations)
        run = engine.drain(engine.create(plan, {"observations": observations}).run_id)
        ref = run.dag.node_states["geometry"].current().outputs["evidence"]
        path = store.blob_path(ref)
        path.unlink()
        run = engine.recover(run.run_id)
        assert run.status == "recovery_blocked"
        assert not path.exists()
        assert calls == {"geometry": 1, "reconstruction": 1}

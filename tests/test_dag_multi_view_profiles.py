from pathlib import Path

import pytest
from test_multi_view_workflow import ContractGeometryFrontend, ContractReconstruction, _plan

from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_multi_view import MultiViewProfile
from assets_generator.dag_profiles import register_multi_view_profiles
from assets_generator.multi_view_relations import register_multi_view_relations
from assets_generator.pipeline import compile_pipeline, load_operator_specs, load_pipeline
from assets_generator.relations import default_relation_registry


def test_multi_view_named_catalog_preserves_profile_identity():
    first = MultiViewProfile(
        _plan(ContractGeometryFrontend(), ContractReconstruction()), {"id": "a"}, test_only=True
    )
    second = MultiViewProfile(first.plan, {"id": "b"}, test_only=True)
    registry = AdapterRegistry()
    register_multi_view_profiles(registry, {"first": first, "second": second}, "first")
    assert len(registry.backend_catalog()) == 6
    relations = default_relation_registry()
    register_multi_view_relations(relations)
    pipeline = load_pipeline(Path("examples/dag-multi-view-asset.yaml"))
    specs = load_operator_specs(Path("examples/dag-multi-view-operators.yaml"))
    default = registry.bind_plan(
        compile_pipeline(pipeline, specs, relation_registry=relations, require_explicit_joins=True),
        relation_registry=relations,
    )
    assert all(binding.backend is None for binding in default.bindings.values())
    from dataclasses import replace

    pipeline = replace(
        pipeline, nodes={key: {**node, "backend": "second"} for key, node in pipeline.nodes.items()}
    )
    explicit = registry.bind_plan(
        compile_pipeline(pipeline, specs, relation_registry=relations, require_explicit_joins=True),
        relation_registry=relations,
    )
    assert all(binding.backend == "second" for binding in explicit.bindings.values())
    assert default.plan_id != explicit.plan_id
    for binding in explicit.bindings.values():
        assert registry.resolve(binding).profile.fingerprint == second.fingerprint


def test_unknown_multi_view_default_rejected():
    with pytest.raises(ValueError, match="unknown multi-view profile"):
        register_multi_view_profiles(AdapterRegistry(), {}, "absent")


def test_named_multi_view_profile_executes_and_restores(tmp_path):
    from dataclasses import replace

    from test_multi_view_workflow import _observations

    from assets_generator.artifact_store import LocalArtifactStore
    from assets_generator.dag_engine import DagEngine
    from assets_generator.dag_persistence import DagRepository

    profile = MultiViewProfile(
        _plan(ContractGeometryFrontend(), ContractReconstruction()), {"id": "named"}, test_only=True
    )
    registry = AdapterRegistry()
    register_multi_view_profiles(registry, {"local-multiview": profile}, "local-multiview")
    relations = default_relation_registry()
    register_multi_view_relations(relations)
    pipeline = load_pipeline(Path("examples/dag-multi-view-asset.yaml"))
    pipeline = replace(
        pipeline,
        nodes={key: {**node, "backend": "local-multiview"} for key, node in pipeline.nodes.items()},
    )
    plan = registry.bind_plan(
        compile_pipeline(
            pipeline,
            load_operator_specs(Path("examples/dag-multi-view-operators.yaml")),
            relation_registry=relations,
            require_explicit_joins=True,
        ),
        relation_registry=relations,
    )
    store = LocalArtifactStore(tmp_path / "store")
    observations = _observations(tmp_path, store)
    with DagRepository(store, tmp_path / "service") as repo:
        engine = DagEngine(repo, registry, relations)
        run = engine.drain(engine.create(plan, {"observations": observations}).run_id)
        assert run.status == "succeeded"
        restored = engine.drain(run.run_id)
        for key, node in run.dag.node_states.items():
            assert len(node.attempts) == 1
            assert restored.dag.node_states[key].attempts == node.attempts

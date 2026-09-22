import io
from copy import deepcopy

import pytest
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_image_resize import ResizeImageAdapter
from assets_generator.dag_persistence import DagRepository
from assets_generator.dag_preflight import analyze_execution
from assets_generator.models import ArtifactRef
from assets_generator.pipeline import (
    _pipeline_from_raw,
    compile_pipeline,
    load_default_operator_specs,
)
from assets_generator.serialization import read_json


@pytest.fixture
def setup(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    registry = AdapterRegistry()
    registry.register(ResizeImageAdapter())
    raw = {
        "pipeline": "preflight",
        "version": "1",
        "inputs": {
            "image": {
                "kind": "rgb_image",
                "carriers": ["artifact_ref"],
                "schema_name": "png",
                "schema_version": "1.0",
            }
        },
        "nodes": {
            "a": {
                "operator": "resize_image@1",
                "adapter": "resize_image@1",
                "parameters": {"width": 4, "height": 4},
                "inputs": {"image": "pipeline.inputs.image"},
            },
            "b": {
                "operator": "resize_image@1",
                "adapter": "resize_image@1",
                "parameters": {"width": 2, "height": 2},
                "inputs": {"image": "a.outputs.image"},
            },
        },
    }

    def plan(doc):
        return registry.bind_plan(
            compile_pipeline(
                _pipeline_from_raw(doc), load_default_operator_specs(), require_explicit_joins=True
            )
        )

    def image(color):
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
        return store.persist_bytes(
            buffer.getvalue(),
            kind="rgb_image",
            schema_name="png",
            schema_version="1.0",
            identity_metadata={"media_type": "image/png", "channel_layout": "RGB"},
        )

    with DagRepository(store, tmp_path / "dag") as repo:
        yield DagEngine(repo, registry), raw, plan, image


def statuses(report):
    return {key: value["status"] for key, value in report["nodes"].items()}


def test_preflight_exact_reuse_readonly_and_invalidation(setup, tmp_path, monkeypatch):
    engine, raw, plan, image = setup
    ref = image("red")
    source = engine.drain(engine.create(plan(raw), {"image": ref}).run_id)
    snapshot = ArtifactRef(**read_json(engine.store.root / "runs" / f"{source.run_id}.json"))
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail("preflight must not execute or persist")

    monkeypatch.setattr(ResizeImageAdapter, "execute", forbidden)
    monkeypatch.setattr(engine, "recover", forbidden)
    monkeypatch.setattr(engine.store, "persist_structured", forbidden)
    monkeypatch.setattr(engine.store, "persist_bytes", forbidden)
    result = analyze_execution(engine, plan(raw), {"image": ref}, snapshot)
    assert statuses(result) == {"a": "reuse", "b": "reuse"}
    assert result["execution_ready"]
    assert result == analyze_execution(engine, plan(raw), {"image": ref}, snapshot)
    changed = deepcopy(raw)
    changed["nodes"]["b"]["parameters"]["width"] = 3
    changed_result = analyze_execution(engine, plan(changed), {"image": ref}, snapshot)
    assert statuses(changed_result) == {"a": "reuse", "b": "execute"}
    assert result["digest"] != changed_result["digest"]
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_unknown_inputs_and_changed_input_do_not_promise_reuse(setup):
    engine, raw, plan, image = setup
    ref = image("red")
    source = engine.drain(engine.create(plan(raw), {"image": ref}).run_id)
    snapshot = ArtifactRef(**read_json(engine.store.root / "runs" / f"{source.run_id}.json"))
    result = analyze_execution(engine, plan(raw), {"image": image("blue")}, snapshot)
    assert statuses(result) == {"a": "execute", "b": "await_upstream"}
    assert result["execution_ready"]
    unknown = analyze_execution(engine, plan(raw), {}, snapshot)
    assert statuses(unknown) == {"a": "await_upstream", "b": "await_upstream"}
    assert not unknown["execution_ready"]
    assert unknown["missing_inputs"] == ["image"]


def test_corrupt_source_is_blocked_without_repair(setup):
    engine, raw, plan, image = setup
    ref = image("red")
    source = engine.drain(engine.create(plan(raw), {"image": ref}).run_id)
    snapshot = ArtifactRef(**read_json(engine.store.root / "runs" / f"{source.run_id}.json"))
    output = source.dag.node_states["b"].current().outputs["image"]
    blob = engine.store.blob_path(output)
    blob.unlink()
    result = analyze_execution(engine, plan(raw), {"image": ref}, snapshot)
    assert statuses(result) == {"a": "blocked", "b": "blocked"}
    assert not result["execution_ready"]
    assert result["error"]["reason"] == "source_snapshot_evidence_invalid"
    assert not blob.exists()
    assert statuses(analyze_execution(engine, plan(raw), {"image": ref})) == {
        "a": "execute",
        "b": "await_upstream",
    }


def test_corrupt_input_blocks_descendants(setup):
    engine, raw, plan, image = setup
    ref = image("red")
    engine.store.blob_path(ref).unlink()
    result = analyze_execution(engine, plan(raw), {"image": ref})
    assert statuses(result) == {"a": "blocked", "b": "blocked"}
    assert not result["execution_ready"]


def test_runtime_relation_rejection_is_explained_without_execution(setup, monkeypatch):
    from assets_generator.contracts import ContractError

    engine, raw, plan, image = setup
    compiled = plan(raw)
    calls = []

    def reject(*args, **kwargs):
        calls.append(True)
        raise ContractError("image and mask lineage mismatch")

    monkeypatch.setattr(engine.relations, "validate_runtime", reject)
    result = analyze_execution(engine, compiled, {"image": image("red")})
    assert calls == [True]
    assert statuses(result) == {"a": "blocked", "b": "blocked"}
    assert "lineage mismatch" in result["nodes"]["a"]["detail"]


def test_changed_registered_implementation_invalidates_old_plan(setup, monkeypatch):
    from assets_generator import dag_adapters

    engine, raw, plan, image = setup
    compiled = plan(raw)
    monkeypatch.setattr(dag_adapters, "adapter_implementation_digest", lambda adapter: "changed")
    result = analyze_execution(engine, compiled, {"image": image("red")})
    assert not result["execution_ready"]
    assert result["error"]["reason"] == "plan_invalid"


def test_selected_subgraph_excludes_unused_inputs_but_source_policy_is_explicit(setup):
    engine, raw, plan, image = setup
    ref = image("red")
    source = engine.drain(engine.create(plan(raw), {"image": ref}).run_id)
    snapshot = ArtifactRef(**read_json(engine.store.root / "runs" / f"{source.run_id}.json"))
    selected = deepcopy(raw)
    del selected["nodes"]["b"]
    assert statuses(analyze_execution(engine, plan(selected), {"image": ref}, snapshot)) == {
        "a": "reuse"
    }
    output = source.dag.node_states["b"].current().outputs["image"]
    engine.store.blob_path(output).unlink()
    report = analyze_execution(engine, plan(selected), {"image": ref}, snapshot)
    assert statuses(report) == {"a": "blocked"}
    assert report["source_evidence_policy"] == "whole_snapshot_closure"
    assert statuses(analyze_execution(engine, plan(selected), {"image": ref})) == {"a": "execute"}

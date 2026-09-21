import io
from copy import deepcopy

import pytest
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_image_resize import ResizeImageAdapter
from assets_generator.dag_persistence import DagRepository
from assets_generator.models import ArtifactRef
from assets_generator.pipeline import (
    _pipeline_from_raw,
    compile_pipeline,
    load_default_operator_specs,
)
from assets_generator.serialization import read_json


def test_reuse_exact_identity_recovery_and_invalidation(tmp_path, monkeypatch):
    registry = AdapterRegistry()
    registry.register(ResizeImageAdapter())
    store = LocalArtifactStore(tmp_path / "store")
    image = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(image, format="PNG")
    ref = store.persist_bytes(
        image.getvalue(),
        kind="rgb_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGB"},
    )
    raw = {
        "pipeline": "reuse",
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

    calls = []
    execute = ResizeImageAdapter.execute

    def spy(self, context):
        calls.append(context.node_id)
        return execute(self, context)

    monkeypatch.setattr(ResizeImageAdapter, "execute", spy)
    with DagRepository(store, tmp_path / "dag") as repo:
        engine = DagEngine(repo, registry)
        original = engine.drain(engine.create(plan(raw), {"image": ref}).run_id)
        assert original.status == "succeeded"
        snapshot = ArtifactRef(**read_json(store.root / "runs" / f"{original.run_id}.json"))
        changed = deepcopy(raw)
        changed["nodes"]["b"]["parameters"]["width"] = 3
        calls.clear()
        reused = engine.drain(
            engine.create(plan(changed), {"image": ref}, reuse_source=snapshot).run_id
        )
        assert reused.status == "succeeded"
        assert calls == ["b"]
        assert reused.dag.node_states["a"].current().reused_from == snapshot
        assert reused.node_attempts[0].execution_mode == "cached"
        assert engine.recover(reused.run_id).status == "succeeded"
        assert calls == ["b"]
        # Different input identities must not reuse even if dimensions match.
        blue = io.BytesIO()
        Image.new("RGB", (8, 8), "blue").save(blue, format="PNG")
        other = store.persist_bytes(
            blue.getvalue(),
            kind="rgb_image",
            schema_name="png",
            schema_version="1.0",
            identity_metadata={"media_type": "image/png", "channel_layout": "RGB"},
        )
        calls.clear()
        replaced = engine.drain(
            engine.create(plan(raw), {"image": other}, reuse_source=snapshot).run_id
        )
        assert replaced.status == "succeeded" and calls == ["a", "b"]
        # Immutable snapshots work even when the source's mutable run index is gone.
        (store.root / "runs" / f"{original.run_id}.json").unlink()
        assert engine.recover(reused.run_id).status == "succeeded"
        second_snapshot = ArtifactRef(**read_json(store.root / "runs" / f"{reused.run_id}.json"))
        calls.clear()
        chained = engine.drain(
            engine.create(plan(changed), {"image": ref}, reuse_source=second_snapshot).run_id
        )
        assert chained.status == "succeeded"
        assert calls == []
        assert engine.recover(chained.run_id).status == "succeeded"
        # Changed upstream parameters invalidate the entire dependent branch.
        changed["nodes"]["a"]["parameters"]["width"] = 5
        calls.clear()
        fresh = engine.drain(
            engine.create(plan(changed), {"image": ref}, reuse_source=snapshot).run_id
        )
        assert fresh.status == "succeeded"
        assert calls == ["a", "b"]
        output = original.dag.node_states["a"].current().outputs["image"]
        store.blob_path(output).unlink()
        with pytest.raises(ValueError):
            engine.create(plan(raw), {"image": ref}, reuse_source=snapshot)
        assert engine.recover(reused.run_id).dag.node_states["a"].status == "recovery_blocked"

import io

import pytest
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_adapters import AdapterRegistry, AdapterSpec, NodeExecutionResult
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_image_mask import ApplyBinaryMaskAdapter
from assets_generator.dag_image_resize import ResizeImageAdapter
from assets_generator.dag_persistence import DagRepository
from assets_generator.models import StructuredValue
from assets_generator.node_editor_execution import NodeEditorExecution
from assets_generator.pipeline import (
    _pipeline_from_raw,
    compile_pipeline,
    load_default_operator_specs,
)
from assets_generator.serialization import read_json, to_primitive


class SegmentationFixture:
    # Uses the reusable identity with a CPU-only implementation, never a real model.
    spec = AdapterSpec("remote_text_segmentation", "1", ("text_segmentation@1",))

    def __init__(self):
        self.calls = 0

    def execute(self, context):
        self.calls += 1
        with Image.open(context.store.blob_path(context.inputs["image"])) as image:
            buffer = io.BytesIO()
            mask_image = Image.new("L", image.size, 255)
            mask_image.putpixel((0, 0), 0)
            mask_image.save(buffer, format="PNG")
        mask = context.store.persist_bytes(
            buffer.getvalue(), kind="binary_mask", schema_name="png", schema_version="1.0"
        )
        candidates = context.store.persist_structured(
            StructuredValue(
                "remote_job_result",
                "TextMaskCandidates",
                "1.0",
                {
                    "image": to_primitive(context.inputs["image"]),
                    "candidates": [{"mask": to_primitive(mask)}],
                },
            )
        )
        return NodeExecutionResult({"mask": mask, "candidates": candidates})


@pytest.fixture
def fixture(tmp_path):
    registry = AdapterRegistry()
    registry.register(ResizeImageAdapter())
    registry.register(SegmentationFixture())
    registry.register(ApplyBinaryMaskAdapter())
    raw = {
        "pipeline": "continue",
        "version": "1",
        "inputs": {
            "photo": {
                "kind": "rgb_image",
                "carriers": ["artifact_ref"],
                "schema_name": "png",
                "schema_version": "1.0",
            }
        },
        "nodes": {
            "extract_foreground": {
                "operator": "resize_image@1",
                "adapter": "resize_image@1",
                "parameters": {"width": 4, "height": 4},
                "inputs": {"image": "pipeline.inputs.photo"},
            },
            "segment": {
                "operator": "text_segmentation@1",
                "adapter": "remote_text_segmentation@1",
                "inputs": {"image": "extract_foreground.outputs.image"},
            },
        },
    }
    store = LocalArtifactStore(tmp_path / "store")
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    ref = store.persist_bytes(
        buffer.getvalue(),
        kind="rgb_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGB"},
    )
    with DagRepository(store, tmp_path / "dag") as repo:
        engine = DagEngine(repo, registry)
        plan = registry.bind_plan(
            compile_pipeline(
                _pipeline_from_raw(raw), load_default_operator_specs(), require_explicit_joins=True
            )
        )
        run = engine.drain(engine.create(plan, {"photo": ref}).run_id)
        service = NodeEditorExecution(engine)
        try:
            yield service, run, read_json(store.root / "runs" / f"{run.run_id}.json")
        finally:
            service.close()


def test_continuation_pins_snapshot_and_original_intermediate_image(fixture, tmp_path):
    service, run, snapshot = fixture
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    result = service.continue_extraction(run.run_id, "segment", snapshot)
    assert result["eligible"], result
    assert result["extract_node_id"] == "extract_foreground_2"
    assert result["pipeline"]["nodes"]["extract_foreground_2"]["inputs"] == {
        "image": "extract_foreground.outputs.image",
        "mask": "segment.outputs.mask",
    }
    assert set(result["input_refs"]) == {"photo"}
    assert result["original_image"] == to_primitive(
        run.dag.node_states["segment"].current().resolved_inputs["image"]
    )
    assert {key: row["status"] for key, row in result["preflight"]["nodes"].items()} == {
        "extract_foreground": "reuse",
        "segment": "reuse",
        "extract_foreground_2": "execute",
    }
    assert before == {
        str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }
    # No dependence on the mutable index after caller selected the exact snapshot.
    (service.engine.store.root / "runs" / f"{run.run_id}.json").unlink()
    assert service.continue_extraction(run.run_id, "segment", snapshot) == result


def test_continuation_refuses_unrelated_node_and_damaged_reuse(fixture):
    service, run, snapshot = fixture
    assert not service.continue_extraction(run.run_id, "extract_foreground", snapshot)["eligible"]
    mask = run.dag.node_states["segment"].current().outputs["mask"]
    service.engine.store.blob_path(mask).unlink()
    result = service.continue_extraction(run.run_id, "segment", snapshot)
    assert not result["eligible"]
    assert "cannot be reused" in result["reason"]
    assert not result["preflight"]["execution_ready"]


def test_continuation_rejects_other_runs_snapshot(fixture):
    service, run, snapshot = fixture
    other = service.engine.create(service.engine._plan(run), run.dag.named_actual_inputs)
    result = service.continue_extraction(other.run_id, "segment", snapshot)
    assert not result["eligible"]
    assert "does not belong" in result["reason"]


def test_http_continuation_checked_execution_preserves_source(fixture, tmp_path):
    import json
    import threading
    import time
    from urllib.error import HTTPError
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    from assets_generator.models import ArtifactRef
    from assets_generator.node_editor import DraftEditor, create_editor_server

    service, original, snapshot = fixture
    server = create_editor_server(DraftEditor(tmp_path / "editor", execution=service), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    route = f"/api/runs/{original.run_id}/continue-extraction/segment"
    adapter = service.engine.registry._adapters["remote_text_segmentation@1"]
    assert adapter.calls == 1
    source_index = service.engine.store.root / "runs" / f"{original.run_id}.json"
    source_bytes = source_index.read_bytes()
    source_blob = service.engine.store.blob_path(ArtifactRef(**snapshot)).read_bytes()
    try:
        for query in ("", "?snapshot=", "?snapshot=a&snapshot=b", "?snapshot=a&unexpected=b"):
            with pytest.raises(HTTPError) as caught:
                urlopen(base + route + query)
            assert caught.value.code == 400
        before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        with urlopen(
            base + route + "?" + urlencode({"snapshot": snapshot["artifact_id"]})
        ) as response:
            proposal = json.load(response)
        assert proposal["eligible"], proposal
        assert service._worker is None
        assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        assert adapter.calls == 1
        body = {key: proposal[key] for key in ("pipeline", "input_refs", "reuse_source")}
        body.update(
            preflight_digest=proposal["preflight"]["digest"], idempotency_key="continue-http"
        )
        request = Request(
            base + "/api/runs",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Origin": base},
        )
        with urlopen(request) as response:
            assert response.status == 202
            created = json.load(response)
        new_id = created["run"]["run_id"]
        assert new_id != original.run_id
        deadline = time.monotonic() + 20
        while True:
            with urlopen(base + "/api/runs/" + new_id) as response:
                result = json.load(response)
            if not result["busy"]:
                break
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert result["run"]["status"] == "succeeded", result
        assert adapter.calls == 1
        states = result["run"]["dag"]["node_states"]
        assert states["segment"]["attempts"][0]["reused_from"] == snapshot
        output = ArtifactRef(
            **states[proposal["extract_node_id"]]["attempts"][0]["outputs"]["rgba"]
        )
        with Image.open(service.engine.store.blob_path(output)) as rgba:
            assert rgba.mode == "RGBA" and rgba.size == (4, 4)
            assert rgba.getpixel((0, 0)) == (255, 0, 0, 0)
            assert rgba.getpixel((1, 0)) == (255, 0, 0, 255)
        assert source_index.read_bytes() == source_bytes
        assert service.engine.store.blob_path(ArtifactRef(**snapshot)).read_bytes() == source_blob
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

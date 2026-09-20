from io import BytesIO
from pathlib import Path

import pytest
import yaml
from PIL import Image
from test_node_editor_execution import wait

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_image_encoding import EncodePngAdapter
from assets_generator.dag_persistence import DagRepository
from assets_generator.node_editor import DraftEditor
from assets_generator.node_editor_execution import NodeEditorExecution


def test_cpu_example_upload_execute_and_reopen(tmp_path):
    graph = yaml.safe_load(
        (Path(__file__).parents[1] / "examples/cpu-image-editor.yaml").read_text()
    )
    registry = AdapterRegistry()
    registry.register(EncodePngAdapter())
    store = LocalArtifactStore(tmp_path / "store")
    source = BytesIO()
    Image.new("RGB", (3, 2), (25, 90, 140)).save(source, format="PNG")
    with DagRepository(store, tmp_path / "runtime") as repo:
        service = NodeEditorExecution(DagEngine(repo, registry))
        try:
            result = DraftEditor(tmp_path / "drafts", execution=service).compile(graph)
            assert result["ok"] and result["execution_ready"]
            uploaded = service.upload_image(source.getvalue())
            started = service.start(graph, image_ref=uploaded["image_ref"])
            run_id = started["run"]["run_id"]
            completed = wait(service, run_id)
            assert completed["run"]["status"] == "succeeded", completed
            attempt = completed["run"]["dag"]["node_states"]["encode"]["attempts"][0]
        finally:
            service.close()
    with DagRepository(store, tmp_path / "runtime") as repo:
        engine = DagEngine(repo, registry)
        recovered = engine.drain(run_id)
        assert recovered.status == "succeeded"
        state = recovered.dag.node_states["encode"]
        assert len(state.attempts) == 1
        assert state.current().attempt == attempt["attempt"]
        output = state.current().outputs["image"]
        with Image.open(store.blob_path(output)) as image:
            assert image.format == "PNG"
            assert image.size == (3, 2)
            assert image.getpixel((0, 0)) == (25, 90, 140)


def test_empty_canvas_can_compile_but_cannot_create_a_successful_run(tmp_path):
    registry = AdapterRegistry()
    registry.register(EncodePngAdapter())
    store = LocalArtifactStore(tmp_path / "store")
    graph = {
        "pipeline": "empty_canvas",
        "version": "1",
        "inputs": {"image": {"kind": "rgb_image", "carriers": ["artifact_ref"]}},
        "nodes": {},
    }
    with DagRepository(store, tmp_path / "runtime") as repo:
        service = NodeEditorExecution(DagEngine(repo, registry))
        try:
            compiled = DraftEditor(tmp_path / "drafts", execution=service).compile(graph)
            assert compiled["ok"] and compiled["bound_plan"]
            assert not compiled["execution_ready"]
            assert "at least one processing node" in compiled["execution_reason"]
            with pytest.raises(ContractError, match="at least one processing node"):
                service.start(graph, "/must-not-import.png")
            assert service.list_runs() == []
        finally:
            service.close()

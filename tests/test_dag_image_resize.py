import io
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from PIL import Image
from test_node_editor_execution import wait

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.dag_adapters import AdapterRegistry, NodeExecutionContext
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_image_encoding import EncodePngAdapter
from assets_generator.dag_image_resize import ResizeImageAdapter
from assets_generator.dag_persistence import DagRepository
from assets_generator.node_editor import DraftEditor
from assets_generator.node_editor_execution import NodeEditorExecution


def test_resize_fanout_parameters_provenance_and_missing_output(tmp_path):
    graph = yaml.safe_load(Path("examples/cpu-image-resize.yaml").read_text())
    registry = AdapterRegistry()
    registry.register(EncodePngAdapter())
    registry.register(ResizeImageAdapter())
    store = LocalArtifactStore(tmp_path / "store")
    image = Image.new("RGB", (2, 1))
    image.putdata([(255, 0, 0), (0, 0, 255)])
    data = io.BytesIO()
    image.save(data, format="PNG")
    graph["nodes"]["thumbnail"]["parameters"] = {"width": 4, "height": 2, "resampling": "nearest"}
    with DagRepository(store, tmp_path / "runtime") as repo:
        engine = DagEngine(repo, registry)
        execution = NodeEditorExecution(engine)
        try:
            editor = DraftEditor(tmp_path / "drafts", execution=execution)
            assert editor.compile(graph)["execution_ready"]
            invalid = deepcopy(graph)
            invalid["nodes"]["thumbnail"]["parameters"]["width"] = 0
            assert not editor.compile(invalid)["ok"]
            invalid = deepcopy(graph)
            invalid["nodes"]["thumbnail"]["inputs"]["image"] = "pipeline.inputs.image"
            assert not editor.compile(invalid)["ok"]
            uploaded = execution.upload_image(data.getvalue())
            started = execution.start(graph, image_ref=uploaded["image_ref"])
            result = wait(execution, started["run"]["run_id"])
            assert result["run"]["status"] == "succeeded", result
            run = repo.load(result["run"]["run_id"])
            small = run.dag.node_states["thumbnail"].current()
            large = run.dag.node_states["model_input"].current()
            assert small.resolved_inputs == large.resolved_inputs
            output = small.outputs["image"]
            with Image.open(store.blob_path(output)) as resized:
                assert resized.size == (4, 2)
                assert [resized.getpixel((x, y)) for y in range(2) for x in range(4)] == [
                    (255, 0, 0)
                ] * 2 + [(0, 0, 255)] * 2 + [(255, 0, 0)] * 2 + [(0, 0, 255)] * 2
            assert output != large.outputs["image"]
            evidence = [store.read_structured(a.provenance["image"][0]) for a in (small, large)]
            assert evidence[0]["provenance_id"] != evidence[1]["provenance_id"]
            assert evidence[0]["parameters"]["node_parameters"]["width"] == 4
            before = deepcopy(run.dag.node_states)
            assert engine.recover(run.run_id).dag.node_states == before
            store.blob_path(output).unlink()
            blocked = engine.recover(run.run_id)
            assert blocked.dag.node_states["thumbnail"].status == "recovery_blocked"
            assert blocked.dag.node_states["model_input"] == before["model_input"]
            assert all(len(s.attempts) == 1 for s in blocked.dag.node_states.values())
            assert not store.blob_path(output).exists()
        finally:
            execution.close()


@pytest.mark.parametrize("mode,format", [("RGBA", "PNG"), ("RGB", "JPEG")])
def test_resize_rejects_mislabeled_encoding(tmp_path, mode, format):
    store = LocalArtifactStore(tmp_path)
    data = io.BytesIO()
    Image.new(mode, (2, 2)).save(data, format=format)
    ref = store.persist_bytes(
        data.getvalue(),
        kind="rgb_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGB"},
    )
    context = NodeExecutionContext(
        "run", "resize", {"image": ref}, {"width": 4, "height": 4, "resampling": "nearest"}, store
    )
    with pytest.raises(ContractError, match="single RGB PNG"):
        ResizeImageAdapter().execute(context)

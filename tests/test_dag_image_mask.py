import io

import pytest
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.dag_adapters import NodeExecutionContext
from assets_generator.dag_image_mask import ApplyBinaryMaskAdapter


def _png(image: Image.Image) -> bytes:
    data = io.BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


def test_apply_binary_mask_creates_rgba_with_exact_alpha(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    image = store.persist_bytes(
        _png(Image.new("RGB", (2, 2), (20, 40, 60))),
        kind="rgb_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGB"},
    )
    mask = store.persist_bytes(
        _png(Image.fromarray(__import__("numpy").array([[255, 0], [0, 255]], dtype="uint8"))),
        kind="binary_mask",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "L"},
    )
    result = (
        ApplyBinaryMaskAdapter()
        .execute(NodeExecutionContext("run", "mask", {"image": image, "mask": mask}, {}, store))
        .outputs["rgba"]
    )
    with Image.open(store.blob_path(result)) as output:
        assert output.mode == "RGBA"
        assert list(output.getchannel("A").tobytes()) == [255, 0, 0, 255]


@pytest.mark.parametrize("mask_values", [[[0, 0], [0, 0]], [[128, 0], [0, 255]]])
def test_apply_binary_mask_rejects_empty_or_non_binary_masks(tmp_path, mask_values):
    store = LocalArtifactStore(tmp_path / "store")
    image = store.persist_bytes(
        _png(Image.new("RGB", (2, 2), "red")),
        kind="rgb_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGB"},
    )
    mask = store.persist_bytes(
        _png(Image.fromarray(__import__("numpy").array(mask_values, dtype="uint8"))),
        kind="binary_mask",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "L"},
    )
    with pytest.raises(ContractError, match="0/255|foreground"):
        ApplyBinaryMaskAdapter().execute(
            NodeExecutionContext("run", "mask", {"image": image, "mask": mask}, {}, store)
        )


def test_mask_editor_compiles_runs_and_recovers_exact_evidence(tmp_path):
    from copy import deepcopy
    from pathlib import Path

    import yaml
    from test_node_editor_execution import wait

    from assets_generator.dag_adapters import AdapterRegistry
    from assets_generator.dag_engine import DagEngine
    from assets_generator.dag_persistence import DagRepository
    from assets_generator.node_editor import DraftEditor
    from assets_generator.node_editor_execution import NodeEditorExecution

    registry = AdapterRegistry()
    registry.register(ApplyBinaryMaskAdapter())
    store = LocalArtifactStore(tmp_path / "store")
    graph = yaml.safe_load(Path("examples/cpu-image-mask.yaml").read_text())
    with DagRepository(store, tmp_path / "runtime") as repo:
        engine = DagEngine(repo, registry)
        execution = NodeEditorExecution(engine)
        try:
            editor = DraftEditor(tmp_path / "drafts", execution=execution)
            assert editor.compile(graph)["execution_ready"]
            image = execution.upload_image(_png(Image.new("RGB", (2, 2), "red")))["image_ref"]
            mask = execution.upload_mask(
                _png(Image.frombytes("L", (2, 2), bytes([255, 0, 0, 255])))
            )["mask_ref"]
            refs = {"image": image, "mask": mask}
            created = execution.start(graph, input_refs=refs, idempotency_key="mask-test")
            run_id = created["run"]["run_id"]
            assert wait(execution, run_id)["run"]["status"] == "succeeded"
            persisted = repo.load(run_id)
            attempt = persisted.dag.node_states["composite"].current()
            output = attempt.outputs["rgba"]
            with Image.open(store.blob_path(output)) as decoded:
                assert decoded.tobytes() == bytes(
                    [255, 0, 0, 255, 255, 0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 255]
                )
            records = [store.read_structured(ref) for ref in attempt.provenance["rgba"]]
            assert set(records[0]["derived_from_artifact_ids"]) == {
                image["artifact_id"],
                mask["artifact_id"],
            }
            reference = execution.output_reference(run_id, "composite", "rgba")
            assert reference["reference"] == {"artifact_id": output.artifact_id}
            assert reference["source_run_id"] == run_id
            assert reference["kind"] == "rgba_image"
            assert repo.load(run_id).dag.revision == persisted.dag.revision
            before = deepcopy(persisted.dag.node_states)
            assert engine.recover(run_id).dag.node_states == before
            assert (
                execution.start(graph, input_refs=refs, idempotency_key="mask-test")["run"][
                    "run_id"
                ]
                == run_id
            )
            store.blob_path(output).unlink()
            with pytest.raises((ValueError, OSError, RuntimeError)):
                execution.output_reference(run_id, "composite", "rgba")
            recovered = engine.recover(run_id)
            assert recovered.dag.node_states["composite"].status == "recovery_blocked"
            assert len(recovered.dag.node_states["composite"].attempts) == 1
            assert not store.blob_path(output).exists()
        finally:
            execution.close()


@pytest.mark.parametrize(
    "damage", ["rgb_color_key", "rgba_mask", "rgb_mask", "mask_color_key", "jpeg_mask"]
)
def test_compositing_rejects_implicit_alpha_or_color_conversion(tmp_path, damage):
    store = LocalArtifactStore(tmp_path / "store")
    source = Image.new("RGB", (2, 2), "red")
    mask = Image.new("L", (2, 2), 255)
    if damage == "rgb_color_key":
        source.info["transparency"] = (255, 0, 0)
    if damage == "rgba_mask":
        mask = Image.new("RGBA", (2, 2), (255, 255, 255, 0))
    if damage == "rgb_mask":
        mask = Image.new("RGB", (2, 2), "white")
    if damage == "mask_color_key":
        mask.info["transparency"] = 255
    mask_bytes = _png(mask)
    if damage == "jpeg_mask":
        data = io.BytesIO()
        mask.save(data, format="JPEG")
        mask_bytes = data.getvalue()
    image_ref = store.persist_bytes(
        _png(source), kind="rgb_image", schema_name="png", schema_version="1.0"
    )
    mask_ref = store.persist_bytes(
        mask_bytes, kind="binary_mask", schema_name="png", schema_version="1.0"
    )
    with pytest.raises(ContractError, match="opaque RGB|grayscale PNG"):
        ApplyBinaryMaskAdapter().execute(
            NodeExecutionContext("run", "mask", {"image": image_ref, "mask": mask_ref}, {}, store)
        )

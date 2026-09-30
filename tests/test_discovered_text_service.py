"""Known SAM3 wire discovery preserves text evidence and offline bindings."""

from copy import deepcopy

import pytest

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.dag_adapters import AdapterRegistry, NodeExecutionContext
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.model_service_adapters import discovered_service_adapter
from assets_generator.model_service_descriptor import validate_descriptor
from assets_generator.node_editor import DraftEditor
from assets_generator.node_editor_execution import NodeEditorExecution
from assets_generator.node_editor_services import ModelServices
from assets_generator.serialization import canonical_json_bytes, sha256_bytes


def descriptor():
    return validate_descriptor(
        {
            "schema_version": "model_service@1",
            "display_name": "Mixed service",
            "service_id": "mixed",
            "backend_digest": "sha256:" + "a" * 64,
            "capabilities": [
                {
                    "capability_id": "mesh",
                    "operator": "shape_generation@1",
                    "transport": "remote_jobs@1",
                    "parameter_schema": {"type": "object", "properties": {}},
                    "defaults": {},
                    "frame_id": "native",
                    "up_axis": "+Z",
                    "unit": "relative_unit",
                },
                {
                    "capability_id": "segment",
                    "operator": "text_segmentation@2",
                    "transport": "sam3_text_jobs@1",
                    "parameter_schema": {
                        "type": "object",
                        "properties": {
                            "confidence": {"type": "number", "minimum": 0.01, "maximum": 0.99}
                        },
                    },
                    "defaults": {"confidence": 0.6},
                },
            ],
        }
    )


def graph(backend):
    return {
        "pipeline": "text",
        "version": "1",
        "inputs": {
            "image": {"kind": "rgb_image", "carriers": ["artifact_ref"]},
            "text": {
                "kind": "text",
                "carriers": ["artifact_ref"],
                "schema_name": "plain_text",
                "schema_version": "1.0",
                "media_type": "text/plain",
            },
        },
        "nodes": {
            "segment": {
                "operator": "text_segmentation@2",
                "backend": backend,
                "inputs": {"image": "pipeline.inputs.image", "text": "pipeline.inputs.text"},
            }
        },
    }


def test_discovery_install_restore_mixed_capabilities(tmp_path, monkeypatch):
    raw = descriptor()
    detection = {
        "endpoint": "http://127.0.0.1:8773",
        "descriptor": raw,
        "descriptor_digest": sha256_bytes(canonical_json_bytes(raw)),
    }
    monkeypatch.setattr(ModelServices, "detect", lambda *args: detection)
    store = LocalArtifactStore(tmp_path / "store")
    with DagRepository(store, tmp_path / "runtime") as repo:
        execution = NodeEditorExecution(DagEngine(repo, AdapterRegistry()))
        try:
            editor = DraftEditor(tmp_path / "editor", execution=execution)
            found = editor.detect_model_service({"endpoint": detection["endpoint"]})
            assert all(item["installable"] for item in found["capability_availability"].values())
            entries = {
                name: editor.add_model_service(
                    {
                        "endpoint": detection["endpoint"],
                        "descriptor_digest": detection["descriptor_digest"],
                        "capability_id": name,
                    }
                )
                for name in ("mesh", "segment")
            }
            assert entries["mesh"]["backend"] != entries["segment"]["backend"]
            compiled = editor.compile(graph(entries["segment"]["backend"]))
            assert compiled["ok"] and compiled["execution_ready"], compiled
            summaries = editor.model_services.summaries()
            assert summaries[1]["operator"] == "text_segmentation@2"
            assert summaries[1]["frame_id"] == "unknown"
            assert summaries[1]["up_axis"] == "unknown"
            assert summaries[1]["unit"] == "unknown"
        finally:
            execution.close()
    monkeypatch.setattr(
        ModelServices, "detect", lambda *args: pytest.fail("offline restore queried service")
    )
    with DagRepository(store, tmp_path / "runtime") as repo:
        execution = NodeEditorExecution(DagEngine(repo, AdapterRegistry()))
        try:
            editor = DraftEditor(tmp_path / "editor", execution=execution)
            assert editor.model_services.summaries() == summaries
            assert (
                editor.compile(graph(entries["segment"]["backend"]))["bound_plan"]
                == compiled["bound_plan"]
            )
        finally:
            execution.close()


def test_payload_keeps_existing_sam3_wire_and_text_artifact(tmp_path):
    adapter = discovered_service_adapter("http://127.0.0.1:8773", descriptor(), "segment")
    store = LocalArtifactStore(tmp_path)
    text = store.persist_bytes(
        b"chair",
        kind="text",
        schema_name="plain_text",
        schema_version="1.0",
        identity_metadata={"media_type": "text/plain"},
    )
    context = NodeExecutionContext(
        "run", "segment", {"text": text}, adapter.spec.normalize_parameters({}), store
    )
    assert adapter.prepare_payload(context) == {
        "operation": "text_segmentation@1",
        "parameters": {"prompt": "chair", "confidence": 0.6},
    }
    store.blob_path(text).unlink()
    with pytest.raises(ContractError, match="intact text"):
        adapter.prepare_payload(context)


@pytest.mark.parametrize(
    "change", ["extra", "prompt", "range", "type", "default", "transport", "required"]
)
def test_incompatible_text_parameter_contract_cannot_install(change):
    raw = deepcopy(descriptor())
    item = raw["capabilities"][1]
    properties = item["parameter_schema"]["properties"]
    if change in {"extra", "prompt"}:
        properties[change] = {"type": "string"}
    elif change == "range":
        properties["confidence"]["maximum"] = 10
    elif change == "type":
        properties["confidence"]["type"] = "string"
    elif change == "default":
        item["defaults"]["confidence"] = 5
    elif change == "transport":
        item["transport"] = "remote_jobs@1"
    else:
        item["parameter_schema"]["required"] = ["prompt"]
    with pytest.raises((ContractError, ValueError)):
        discovered_service_adapter("http://127.0.0.1:8773", raw, "segment")

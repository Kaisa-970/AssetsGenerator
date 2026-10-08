"""Regression coverage for discovered capability boundaries, without GPU models."""

import struct
from dataclasses import replace

import pytest
import trimesh

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.compiled_plan import CompiledPlan
from assets_generator.contracts import (
    ContractError,
    PortSpec,
    validate_operator_outputs,
    validate_port_value,
)
from assets_generator.dag_adapters import NodeExecutionContext
from assets_generator.model_service_adapters import (
    GenericRemoteCapabilityAdapter,
    discovered_service_adapter,
)
from assets_generator.model_service_descriptor import dynamic_operator_spec, validate_descriptor
from assets_generator.models import StructuredValue
from assets_generator.pipeline import PipelineDefinition, compile_pipeline
from assets_generator.remote_protocol import RemoteIdentity, RemoteJob
from assets_generator.sam3d_discovery import sam3d_descriptor
from assets_generator.serialization import canonical_json_bytes, sha256_bytes, to_primitive


def descriptor(output=None):
    return {
        "schema_version": "model_service@1",
        "display_name": "Review",
        "service_id": "review",
        "backend_digest": "sha256:" + "a" * 64,
        "capabilities": [
            {
                "capability_id": "review",
                "transport": "remote_jobs@1",
                "parameter_schema": {"type": "object", "properties": {}},
                "defaults": {},
                "inputs": {
                    "prompt": {
                        "kind": "text",
                        "carrier": "artifact_ref",
                        "schema_name": "plain_text",
                        "schema_version": "1.0",
                        "media_type": "text/plain",
                    }
                },
                "outputs": {
                    "result": output
                    or {
                        "kind": "semantic_info",
                        "carrier": "structured",
                        "schema_name": "SemanticInfo",
                        "schema_version": "1.0",
                        "media_type": "application/json",
                    }
                },
            }
        ],
    }


def import_output(tmp_path, data, output=None):
    raw = descriptor(output)
    adapter = GenericRemoteCapabilityAdapter("http://localhost:1234", raw, "review")
    store = LocalArtifactStore(tmp_path / "store")
    context = NodeExecutionContext("run", "node", {}, {}, store)
    port = adapter.capability["outputs"]["result"]
    job = RemoteJob(
        "job",
        "succeeded",
        canonical_json_bytes(
            {
                "outputs": [
                    {
                        "output_id": "result",
                        "blob_digest": sha256_bytes(data),
                        "byte_length": len(data),
                        "media_type": port.get("media_type", "application/json"),
                    }
                ]
            }
        ),
        None,
    )
    result = adapter.import_result(context, job, {"result": data})
    validate_operator_outputs(
        dynamic_operator_spec(adapter.capability), dict(result.outputs), store
    )
    return result, store


def test_sam3d_discovery_available_and_pins_identity():
    identity = RemoteIdentity("sam3d", "sha256:" + "a" * 64)
    raw = sam3d_descriptor(identity, "sha256:" + "b" * 64)
    adapter = discovered_service_adapter("http://localhost:8772", raw, "masked_shape_generation")
    assert adapter.spec.defaults["upstream_digest"] == "sha256:" + "b" * 64
    raw["capabilities"][0]["unit"] = "meter"
    with pytest.raises(ContractError, match="mismatch"):
        discovered_service_adapter("http://localhost:8772", raw, "masked_shape_generation")


def test_structured_json_import_and_downstream_round_trip(tmp_path):
    value = {"semantic_class": "chair", "source": "model"}
    result, store = import_output(tmp_path, canonical_json_bytes(value))
    structured = result.outputs["result"]
    assert isinstance(structured, StructuredValue) and structured.value == value
    spec = PortSpec(("semantic_info",), carriers=("structured",), media_type="application/json")
    validate_port_value(
        operator="consumer", port_name="info", spec=spec, value=structured, store=store
    )
    restored = StructuredValue(**to_primitive(structured))
    assert restored == structured


@pytest.mark.parametrize(
    "data",
    [
        b"{}",
        b'{"semantic_class":42,"source":"model"}',
        b'{"semantic_class":null,"source":""}',
        b'{"semantic_class":null,"source":"a","source":"b"}',
        b'{"semantic_class":NaN,"source":"a"}',
    ],
)
def test_structured_rejects_invalid_schema_content(tmp_path, data):
    with pytest.raises(ContractError):
        import_output(tmp_path, data)


@pytest.mark.parametrize("cardinality", ["zero_or_one", "one_or_more", "zero_or_more", "many"])
def test_unsupported_output_cardinality_rejected_at_discovery(cardinality):
    raw = descriptor()
    raw["capabilities"][0]["outputs"]["result"]["cardinality"] = cardinality
    with pytest.raises(ContractError, match="cardinality one"):
        validate_descriptor(raw)


def test_unregistered_structured_schema_rejected_at_discovery():
    raw = descriptor()
    raw["capabilities"][0]["outputs"]["result"]["schema_name"] = "Unknown"
    with pytest.raises(ContractError, match="content validator"):
        validate_descriptor(raw)


def test_structured_input_rejects_non_json_media_type():
    raw = descriptor()
    raw["capabilities"][0]["inputs"]["prompt"] = {
        "kind": "semantic_info",
        "carrier": "structured",
        "schema_name": "SemanticInfo",
        "schema_version": "1.0",
        "media_type": "image/png",
    }
    with pytest.raises(ContractError, match="structured media_type"):
        validate_descriptor(raw)


def mesh_port():
    return {
        "kind": "triangle_mesh",
        "carrier": "artifact_ref",
        "schema_name": "glTF",
        "schema_version": "2.0",
        "media_type": "model/gltf-binary",
        "frame_id": "native",
        "unit": "meter",
    }


@pytest.mark.parametrize("field,bad", [("unit", "relative_unit"), ("frame_id", "other")])
@pytest.mark.parametrize("cardinality", ["one", "one_or_more"])
def test_spatial_exact_contract_compile_runtime_and_roundtrip(tmp_path, field, bad, cardinality):
    raw = descriptor()
    raw["capabilities"][0]["inputs"] = {"mesh": {**mesh_port(), "cardinality": cardinality}}
    cap = validate_descriptor(raw)["capabilities"][0]
    operator = dynamic_operator_spec(cap)
    target = operator.inputs["mesh"]
    pipeline = PipelineDefinition(
        "review",
        "1",
        {"mesh": target},
        {
            "node": {"operator": f"{operator.name}@1", "inputs": {"mesh": "pipeline.inputs.mesh"}},
        },
    )
    compiled = compile_pipeline(pipeline, {f"{operator.name}@1": operator})
    assert CompiledPlan.from_dict(compiled.to_dict()).to_dict() == compiled.to_dict()
    with pytest.raises(ContractError, match=field):
        compile_pipeline(
            replace(pipeline, inputs={"mesh": replace(target, **{field: bad})}),
            {f"{operator.name}@1": operator},
        )
    store = LocalArtifactStore(tmp_path / "store")
    ref = store.persist_bytes(
        b"mesh",
        kind="triangle_mesh",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={
            "media_type": "model/gltf-binary",
            "frame_id": "native",
            "unit": "meter",
            field: bad,
        },
    )
    value = [ref] if cardinality == "one_or_more" else ref
    with pytest.raises(ContractError, match=field):
        validate_port_value(
            operator="node", port_name="mesh", spec=target, value=value, store=store
        )
    adapter = GenericRemoteCapabilityAdapter("http://localhost:1234", raw, "review")
    with pytest.raises(ContractError, match=field):
        adapter.input_blobs(NodeExecutionContext("run", "node", {"mesh": value}, {}, store))


def test_mesh_loaded_before_publishing(tmp_path):
    data = trimesh.creation.box().export(file_type="glb")
    result, store = import_output(tmp_path, data, mesh_port())
    assert store.verify_digest(result.outputs["result"])


def test_invalid_accessor_rejected_before_artifact_creation(tmp_path):
    raw = {
        "asset": {"version": "2.0"},
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 999},
                        "indices": 998,
                    }
                ]
            }
        ],
        "accessors": [],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    body = canonical_json_bytes(raw)
    body += b" " * (-len(body) % 4)
    data = struct.pack("<4sIIII", b"glTF", 2, 20 + len(body), len(body), 0x4E4F534A) + body
    with pytest.raises(ContractError, match="mesh GLB"):
        import_output(tmp_path, data, mesh_port())


def test_absent_spatial_constraints_preserve_legacy_serialized_port():
    raw = to_primitive(PortSpec(("rgb_image",)))
    assert "frame_id" not in raw and "unit" not in raw


def test_structured_output_executes_through_http_dag_and_recovers(tmp_path):
    import threading

    from assets_generator.dag_adapters import AdapterRegistry
    from assets_generator.dag_engine import DagEngine
    from assets_generator.dag_persistence import DagRepository
    from assets_generator.remote_service_http import create_remote_server
    from assets_generator.remote_service_store import RemoteServiceStore
    from assets_generator.remote_service_worker import ServiceOutput, execute_next_service_job

    raw = descriptor()
    identity = RemoteIdentity(raw["service_id"], raw["backend_digest"])
    service = RemoteServiceStore(tmp_path / "service.sqlite", identity)
    server = create_remote_server(service, port=0, descriptor=raw)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    store = LocalArtifactStore(tmp_path / "store")
    try:
        adapter = GenericRemoteCapabilityAdapter(
            f"http://127.0.0.1:{server.server_port}", raw, "review"
        )
        operator = dynamic_operator_spec(adapter.capability)
        registry = AdapterRegistry()
        registry.register(adapter)
        pipeline = PipelineDefinition(
            "structured",
            "1",
            {"prompt": operator.inputs["prompt"]},
            {
                "node": {
                    "operator": f"{operator.name}@1",
                    "inputs": {"prompt": "pipeline.inputs.prompt"},
                },
            },
        )
        plan = registry.bind_plan(
            compile_pipeline(
                pipeline,
                {f"{operator.name}@1": operator},
                require_explicit_joins=True,
            )
        )
        text = store.persist_bytes(
            b"chair",
            kind="text",
            schema_name="plain_text",
            schema_version="1.0",
            identity_metadata={"media_type": "text/plain"},
        )
        with DagRepository(store, tmp_path / "runtime") as repo:
            engine = DagEngine(repo, registry)
            pending = engine.drain(engine.create(plan, {"prompt": text}).run_id)
            assert pending.status == "running"
            execute_next_service_job(
                service,
                lambda request, repository: {
                    "result": ServiceOutput(
                        canonical_json_bytes({"semantic_class": "chair", "source": "model"}),
                        "application/json",
                    ),
                },
            )
            done = engine.drain(pending.run_id)
            assert done.status == "succeeded"
            assert isinstance(
                done.dag.node_states["node"].current().outputs["result"], StructuredValue
            )
        with DagRepository(store, tmp_path / "runtime") as repo:
            restored = DagEngine(repo, registry).drain(pending.run_id)
            assert restored.dag.node_states == done.dag.node_states
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        service.close()


def test_multiple_scalar_outputs_are_imported_and_validated(tmp_path):
    from copy import deepcopy

    raw = descriptor()
    raw["capabilities"][0]["outputs"]["second"] = deepcopy(
        raw["capabilities"][0]["outputs"]["result"]
    )
    adapter = GenericRemoteCapabilityAdapter("http://localhost:1234", raw, "review")
    store = LocalArtifactStore(tmp_path / "store")
    blobs = {
        name: canonical_json_bytes({"semantic_class": name, "source": "model"})
        for name in ("result", "second")
    }
    job = RemoteJob(
        "job",
        "succeeded",
        canonical_json_bytes(
            {
                "outputs": [
                    {
                        "output_id": name,
                        "blob_digest": sha256_bytes(data),
                        "byte_length": len(data),
                        "media_type": "application/json",
                    }
                    for name, data in blobs.items()
                ]
            }
        ),
        None,
    )
    result = adapter.import_result(NodeExecutionContext("run", "node", {}, {}, store), job, blobs)
    assert set(result.outputs) == set(blobs)
    validate_operator_outputs(
        dynamic_operator_spec(adapter.capability), dict(result.outputs), store
    )


@pytest.mark.parametrize(
    "corruption", ["nonfinite_vertex", "out_of_range_index", "truncated_buffer"]
)
def test_mesh_binary_geometry_corruption_rejected(tmp_path, corruption):
    import json

    data = bytearray(trimesh.creation.box().export(file_type="glb"))
    json_size = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20 : 20 + json_size])
    binary_start = 20 + json_size + 8
    primitive = document["meshes"][0]["primitives"][0]
    accessor_id = (
        primitive["attributes"]["POSITION"]
        if corruption == "nonfinite_vertex"
        else primitive["indices"]
    )
    accessor = document["accessors"][accessor_id]
    view = document["bufferViews"][accessor["bufferView"]]
    offset = binary_start + view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    if corruption == "nonfinite_vertex":
        struct.pack_into("<f", data, offset, float("nan"))
    elif corruption == "out_of_range_index":
        fmt = {5121: "B", 5123: "H", 5125: "I"}[accessor["componentType"]]
        struct.pack_into("<" + fmt, data, offset, 255)
    else:
        del data[-16:]
        struct.pack_into("<I", data, 8, len(data))
        struct.pack_into("<I", data, 20 + json_size, len(data) - binary_start)
    with pytest.raises(ContractError, match="mesh GLB"):
        import_output(tmp_path, bytes(data), mesh_port())

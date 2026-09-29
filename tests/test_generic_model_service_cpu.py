"""A dynamically discovered unknown capability executes through real CPU HTTP."""

import importlib.util
import threading
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_adapters import AdapterRegistry, BoundDagPlan
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_image_resize import ResizeImageAdapter
from assets_generator.dag_persistence import DagRepository
from assets_generator.node_editor import DraftEditor
from assets_generator.node_editor_execution import NodeEditorExecution
from assets_generator.remote_http import RemoteJobClient


def fixture_module():
    path = Path(__file__).parents[1] / "examples/generic_model_service_cpu.py"
    spec = importlib.util.spec_from_file_location("generic_cpu_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def serve(directory, port=0):
    service = fixture_module().CpuGenericService(directory, port)
    thread = threading.Thread(target=service.server.serve_forever, daemon=True)
    thread.start()
    try:
        yield service
    finally:
        service.server.shutdown()
        thread.join(timeout=5)
        service.server.server_close()
        service.store.close()


def test_discover_dynamic_text_to_image_resize_and_recover_offline(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    text = store.persist_bytes(
        b"chair",
        kind="text",
        schema_name="plain_text",
        schema_version="1.0",
        identity_metadata={"media_type": "text/plain"},
    )
    with serve(tmp_path / "service") as service:
        port = service.server.server_port
        registry = AdapterRegistry()
        registry.register(ResizeImageAdapter())
        with DagRepository(store, tmp_path / "runtime") as repo:
            engine = DagEngine(repo, registry)
            execution = NodeEditorExecution(engine)
            try:
                editor = DraftEditor(tmp_path / "editor", execution=execution)
                detected = editor.detect_model_service({"endpoint": service.endpoint})
                installed = editor.add_model_service(
                    {
                        "endpoint": detected["endpoint"],
                        "descriptor_digest": detected["descriptor_digest"],
                        "capability_id": "text_to_image",
                    }
                )
                operator = installed["catalog"]["model_services"][0]["operator"]
                assert operator.startswith("remote_capability_")
                assert service.store.list_jobs()["jobs"] == []
                graph = {
                    "pipeline": "generic_text_image_resize",
                    "version": "1",
                    "inputs": {
                        "prompt": {
                            "kind": "text",
                            "carriers": ["artifact_ref"],
                            "schema_name": "plain_text",
                            "schema_version": "1.0",
                        }
                    },
                    "nodes": {
                        "generate": {
                            "operator": operator,
                            "backend": installed["backend"],
                            "inputs": {"prompt": "pipeline.inputs.prompt"},
                            "parameters": {"width": 17, "height": 13, "seed": 9},
                        },
                        "resize": {
                            "operator": "resize_image@1",
                            "inputs": {"image": "generate.outputs.image"},
                            "parameters": {"width": 8, "height": 6, "resampling": "nearest"},
                        },
                    },
                }
                compiled = editor.compile(graph)
                assert compiled["ok"] and compiled["execution_ready"], compiled
                plan = BoundDagPlan.from_dict(compiled["bound_plan"], registry=engine.registry)
                pending = engine.drain(engine.create(plan, {"prompt": text}).run_id)
                assert pending.dag.node_states["generate"].status == "running"
                assert pending.dag.node_states["resize"].status == "pending"
                binding = pending.dag.node_states["generate"].current().remote_binding
                assert service.execute_next().state == "succeeded"
                assert service.execute_next() is None
                # Service publication precedes Core collection; restart both first.
            finally:
                execution.close()

    with serve(tmp_path / "service", port) as service:
        registry = AdapterRegistry()
        registry.register(ResizeImageAdapter())
        with DagRepository(store, tmp_path / "runtime") as repo:
            engine = DagEngine(repo, registry)
            execution = NodeEditorExecution(engine)
            try:
                restored_editor = DraftEditor(tmp_path / "editor", execution=execution)
                assert (
                    restored_editor.catalog()["model_services"][0]["backend"]
                    == installed["backend"]
                )
                registry = engine.registry
                completed = engine.drain(pending.run_id)
                assert completed.status == "succeeded", completed
                assert all(len(node.attempts) == 1 for node in completed.dag.node_states.values())
                generated = completed.dag.node_states["generate"].current().outputs["image"]
                resized = completed.dag.node_states["resize"].current().outputs["image"]
                for ref, size in ((generated, (17, 13)), (resized, (8, 6))):
                    assert store.verify_digest(ref)
                    with Image.open(store.blob_path(ref)) as image:
                        assert image.format == "PNG" and image.mode == "RGB" and image.size == size
                assert generated != resized
                assert service.execute_next() is None
                assert len(service.store.list_jobs()["jobs"]) == 1
                request = binding.request()
                assert RemoteJobClient(service.endpoint).lookup(request).state == "succeeded"
                repo.verify_reference_closure(
                    completed.dag.node_states["generate"].current().remote_result
                )
            finally:
                execution.close()
    # Historical outputs and completion recovery need no live service.
    with DagRepository(store, tmp_path / "runtime") as repo:
        restored = DagEngine(repo, registry).recover(pending.run_id)
        assert restored.dag.node_states == completed.dag.node_states


def test_invalid_prompt_does_not_block_next_valid_job(tmp_path):
    from copy import deepcopy

    from assets_generator.remote_protocol import RemoteRequest
    from assets_generator.serialization import canonical_json_bytes, sha256_bytes

    store = LocalArtifactStore(tmp_path / "artifacts")
    ref = store.persist_bytes(
        b"table",
        kind="text",
        schema_name="plain_text",
        schema_version="1.0",
        identity_metadata={"media_type": "text/plain"},
    )
    manifest = store.get_manifest(ref.artifact_id)
    import json

    upload = {
        "artifact_id": ref.artifact_id,
        "identity": json.loads(canonical_json_bytes(manifest.identity)),
    }
    payload = {
        "capability_id": "text_to_image",
        "inputs": {"prompt": {"artifact_id": ref.artifact_id}},
        "input_blobs": {"prompt": upload},
        "parameters": {"width": 10, "height": 7, "seed": 1},
    }
    invalid = deepcopy(payload)
    invalid["inputs"]["prompt"]["artifact_id"] = "sha256:" + "0" * 64
    with serve(tmp_path / "service") as service:
        client = RemoteJobClient(service.endpoint)
        client.upload_blob(service.store.identity, b"table", sha256_bytes(b"table"))
        bad = RemoteRequest.create(service.store.identity, "bad-prompt", invalid)
        good = RemoteRequest.create(service.store.identity, "good-prompt", payload)
        client.submit(bad)
        client.submit(good)
        assert service.execute_next().state == "failed"
        assert service.execute_next().state == "succeeded"
        assert client.lookup(bad).state == "failed"
        result = client.lookup(good)
        data = client.download(good, result.job_id, "image", expected_job=result)
        import io

        with Image.open(io.BytesIO(data)) as image:
            assert image.mode == "RGB" and image.size == (10, 7)
        assert service.execute_next() is None

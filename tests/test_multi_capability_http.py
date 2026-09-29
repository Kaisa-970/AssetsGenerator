"""Two installed capabilities retain their bindings and execute over real CPU HTTP."""

import io
import json
import socket
import threading
import time

import pytest
import trimesh
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_adapters import AdapterRegistry, BoundDagPlan, NodeExecutionContext
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.models import BackendNativeFrame
from assets_generator.node_editor import DraftEditor
from assets_generator.node_editor_execution import NodeEditorExecution
from assets_generator.remote_http import RemoteJobClient
from assets_generator.remote_protocol import RemoteRequest
from assets_generator.remote_shape_output import import_shape_output
from assets_generator.serialization import canonical_json_bytes, sha256_bytes, to_primitive
from assets_generator.shape_model_service import ShapeModelService


def test_http_multi_capability_install_offline_restore_and_execute(tmp_path, monkeypatch):
    calls = []

    def inference(capability):
        def infer(path, parameters):
            with Image.open(path) as image:
                assert image.mode == "RGBA"
                assert image.getpixel((0, 0)) == (255, 0, 0, 255)
            calls.append((capability, dict(parameters)))
            return trimesh.creation.box(extents=[parameters["width"], 1, 1]).export(file_type="glb")

        return infer

    def unexpected_fallback(*args):
        raise AssertionError("capability routing used the fallback inference")

    capabilities = {
        name: {
            "display_name": name,
            "parameter_schema": {
                "type": "object",
                "properties": {"width": {"type": "number", "minimum": 0.1}},
            },
            "defaults": {"width": width},
        }
        for name, width in (("mesh_fast", 1.0), ("mesh_quality", 3.0))
    }
    frame = BackendNativeFrame("cpu-native", "right", "+Z", None, "unknown", "relative_unit")
    service = ShapeModelService(
        service_id="multi-capability-http",
        display_name="CPU multi-capability fixture",
        deployment={"model": "cpu-box-v1"},
        frame=frame,
        infer=unexpected_fallback,
        infer_by_capability={name: inference(name) for name in capabilities},
        capabilities=capabilities,
        directory=tmp_path / "service",
    )
    thread = threading.Thread(target=service.server.serve_forever, daemon=True)
    thread.start()
    store = LocalArtifactStore(tmp_path / "store")
    plans = {}
    backends = {}
    try:
        with DagRepository(store, tmp_path / "runtime") as repo:
            execution = NodeEditorExecution(DagEngine(repo, AdapterRegistry()))
            try:
                editor = DraftEditor(tmp_path / "editor", execution=execution)
                detected = editor.detect_model_service({"endpoint": service.endpoint})
                assert {
                    item["capability_id"] for item in detected["descriptor"]["capabilities"]
                } == set(capabilities)
                for name in capabilities:
                    added = editor.add_model_service(
                        {
                            "endpoint": detected["endpoint"],
                            "descriptor_digest": detected["descriptor_digest"],
                            "capability_id": name,
                        }
                    )
                    backends[name] = added["backend"]
                    compiled = editor.compile(
                        {
                            "pipeline": "multi_capability",
                            "version": "1",
                            "inputs": {
                                "image": {"kind": "rgba_image", "carriers": ["artifact_ref"]}
                            },
                            "nodes": {
                                "shape": {
                                    "operator": "shape_generation@1",
                                    "backend": added["backend"],
                                    "inputs": {"image": "pipeline.inputs.image"},
                                }
                            },
                        }
                    )
                    assert compiled["ok"] and compiled["execution_ready"]
                    plans[name] = compiled["bound_plan"]
                assert len(set(backends.values())) == 2
                assert calls == []  # Discovery and installation must never infer.
            finally:
                execution.close()

        # Reconstruct both the editor catalog and Registry without any network access.
        with DagRepository(store, tmp_path / "runtime") as repo:
            execution = NodeEditorExecution(DagEngine(repo, AdapterRegistry()))
            try:
                with monkeypatch.context() as offline:
                    offline.setattr(
                        socket,
                        "create_connection",
                        lambda *args, **kwargs: pytest.fail("catalog restoration accessed network"),
                    )
                    restored = DraftEditor(tmp_path / "editor", execution=execution)
                    summaries = restored.catalog()["model_services"]
                    assert {
                        item["capability_id"]: item["backend"] for item in summaries
                    } == backends
                    registry = execution.engine.registry
                    bound = {
                        name: BoundDagPlan.from_dict(plan, registry=registry)
                        for name, plan in plans.items()
                    }
                    assert {name: plan.to_dict() for name, plan in bound.items()} == plans

                buffer = io.BytesIO()
                Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(buffer, format="PNG")
                image_ref = store.persist_bytes(
                    buffer.getvalue(),
                    kind="rgba_image",
                    schema_name="png",
                    schema_version="1.0",
                    identity_metadata={"media_type": "image/png", "channel_layout": "RGBA"},
                )
                image_identity = store.get_manifest(image_ref.artifact_id).identity
                client = RemoteJobClient(service.endpoint)
                client.upload_blob(service.identity, buffer.getvalue(), image_identity.blob_digest)
                service.start_worker()
                outputs = {}
                for name, plan in bound.items():
                    binding = plan.bindings["shape"]
                    adapter = registry.resolve(binding)
                    context = NodeExecutionContext(
                        run_id="cpu-http",
                        node_id=name,
                        inputs={"image": image_ref},
                        parameters=binding.parameters,
                        store=store,
                    )
                    payload = adapter.prepare_payload(context)
                    assert payload["capability_id"] == name
                    assert payload["parameters"] == capabilities[name]["defaults"]
                    request = RemoteRequest.create(
                        service.identity,
                        "cpu-http-" + name,
                        {
                            **payload,
                            "input_digest": sha256_bytes(
                                canonical_json_bytes(to_primitive(image_ref))
                            ),
                            "binding_digest": sha256_bytes(canonical_json_bytes(binding.to_dict())),
                            "input_blobs": {
                                "rgba": {
                                    "artifact_id": image_ref.artifact_id,
                                    "identity": to_primitive(image_identity),
                                }
                            },
                        },
                    )
                    client.submit(request)
                    deadline = time.monotonic() + 10
                    while True:
                        job = client.lookup(request)
                        if job is not None and job.state in {"succeeded", "failed"}:
                            break
                        assert time.monotonic() < deadline, "CPU HTTP inference did not finish"
                        time.sleep(0.02)
                    assert job.state == "succeeded", job.error_json
                    assert client.submit(request).state == "succeeded"
                    blobs = {
                        output: client.download(request, job.job_id, output, expected_job=job)
                        for output in ("mesh", "shape_metadata")
                    }
                    result = import_shape_output(store, blobs)
                    assert store.verify_digest(result.mesh)
                    assert result.native_frame.value == to_primitive(frame)
                    assert result.backend_metadata["parameters"] == capabilities[name]["defaults"]
                    assert (
                        result.backend_metadata["backend_digest"] == service.identity.backend_digest
                    )
                    scene = trimesh.load(io.BytesIO(blobs["mesh"]), file_type="glb", force="scene")
                    assert scene.extents == pytest.approx(
                        [capabilities[name]["defaults"]["width"], 1, 1]
                    )
                    outputs[name] = result.mesh.artifact_id
                    assert json.loads(request.payload_json)["capability_id"] == name
                assert len(set(outputs.values())) == 2
                assert calls == [(name, capabilities[name]["defaults"]) for name in capabilities]
            finally:
                execution.close()
    finally:
        service.server.shutdown()
        thread.join(timeout=5)
        service.close()

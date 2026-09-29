"""Installed services are explicit, pinned, durable and do not modify old bindings."""

import json

import pytest

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.node_editor import DraftEditor
from assets_generator.node_editor_execution import NodeEditorExecution
from assets_generator.node_editor_services import ModelServices
from assets_generator.serialization import canonical_json_bytes, sha256_bytes


def detected(name="One", identity="a"):
    descriptor = {
        "schema_version": "model_service@1",
        "display_name": name,
        "service_id": "shape-one",
        "backend_digest": "sha256:" + identity * 64,
        "operator": "shape_generation@1",
        "transport": "remote_jobs@1",
        "frame_id": "model-native",
        "up_axis": "+Y",
        "unit": "relative_unit",
        "parameter_schema": {
            "type": "object",
            "properties": {"steps": {"type": "integer", "minimum": 1}},
        },
        "defaults": {"steps": 4},
    }
    return {
        "endpoint": "http://127.0.0.1:8999",
        "descriptor": descriptor,
        "descriptor_digest": sha256_bytes(canonical_json_bytes(descriptor)),
    }


def graph(backend):
    return {
        "pipeline": "new_model",
        "version": "1",
        "inputs": {"image": {"kind": "rgba_image", "carriers": ["artifact_ref"]}},
        "nodes": {
            "shape": {
                "operator": "shape_generation@1",
                "backend": backend,
                "inputs": {"image": "pipeline.inputs.image"},
            }
        },
    }


def test_install_restore_and_changed_descriptor_leave_old_binding(tmp_path, monkeypatch):
    current = detected()
    calls = []
    monkeypatch.setattr(
        ModelServices, "detect", lambda self, endpoint: calls.append(endpoint) or current
    )
    store = LocalArtifactStore(tmp_path / "store")
    with DagRepository(store, tmp_path / "runtime") as repo:
        execution = NodeEditorExecution(DagEngine(repo, AdapterRegistry()))
        editor = DraftEditor(
            tmp_path / "editor", execution=execution, execution_profile="CPU · 未配置模型"
        )
        detection = editor.detect_model_service({"endpoint": current["endpoint"]})
        assert editor.catalog()["model_services"] == []
        assert not editor.model_services.path.exists()
        added = editor.add_model_service(
            {"endpoint": detection["endpoint"], "descriptor_digest": detection["descriptor_digest"]}
        )
        assert added["catalog"]["execution_profile"] == "已配置模型服务"
        backend = added["backend"]
        compiled = editor.compile(graph(backend))
        assert compiled["ok"] and compiled["execution_ready"]
        assert len(calls) == 2
        again = editor.add_model_service(
            {"endpoint": detection["endpoint"], "descriptor_digest": detection["descriptor_digest"]}
        )
        assert again["backend"] == backend
        assert len(editor.catalog()["model_services"]) == 1
        current = detected("Two", "b")
        with pytest.raises(ValueError, match="变化"):
            editor.add_model_service(
                {
                    "endpoint": detection["endpoint"],
                    "descriptor_digest": detection["descriptor_digest"],
                }
            )
        second = editor.add_model_service(
            {"endpoint": current["endpoint"], "descriptor_digest": current["descriptor_digest"]}
        )
        assert second["backend"] != backend
        assert editor.compile(graph(backend))["bound_plan"] == compiled["bound_plan"]
        execution.close()
    # Restoration is entirely offline and the pinned descriptor re-creates the binding.
    monkeypatch.setattr(
        ModelServices, "detect", lambda *a: pytest.fail("restore performed network")
    )
    with DagRepository(store, tmp_path / "runtime") as repo:
        execution = NodeEditorExecution(DagEngine(repo, AdapterRegistry()))
        editor = DraftEditor(tmp_path / "editor", execution=execution)
        assert len(editor.catalog()["model_services"]) == 2
        assert editor.compile(graph(backend))["bound_plan"] == compiled["bound_plan"]
        execution.close()


def test_failed_durable_install_does_not_change_live_registry(tmp_path, monkeypatch):
    detection = detected()
    monkeypatch.setattr(ModelServices, "detect", lambda *a: detection)
    with DagRepository(LocalArtifactStore(tmp_path / "store"), tmp_path / "runtime") as repo:
        execution = NodeEditorExecution(DagEngine(repo, AdapterRegistry()))
        editor = DraftEditor(tmp_path / "editor", execution=execution)
        registry = execution.engine.registry

        def fail(*a, **kw):
            raise OSError("simulated write failure")

        monkeypatch.setattr(editor.model_services.io, "write", fail)
        with pytest.raises(OSError):
            editor.add_model_service(
                {
                    "endpoint": detection["endpoint"],
                    "descriptor_digest": detection["descriptor_digest"],
                }
            )
        assert execution.engine.registry is registry
        assert registry.backend_catalog() == []
        assert editor.catalog()["model_services"] == []
        with pytest.raises(ValueError, match="重启"):
            editor.add_model_service(
                {
                    "endpoint": detection["endpoint"],
                    "descriptor_digest": detection["descriptor_digest"],
                }
            )
        execution.close()


def test_tampered_install_file_is_rejected(tmp_path):
    item = detected()
    item["backend"] = "not_the_derived_identity"
    path = tmp_path / "model-services" / "installed.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps({"schema_version": "installed_model_services@1", "services": [item]})
    )
    with pytest.raises(ValueError, match="identity mismatch"):
        ModelServices(tmp_path)


def test_http_detect_add_and_closed_input(tmp_path, monkeypatch):
    import threading
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from assets_generator.node_editor import create_editor_server

    detection = detected()
    monkeypatch.setattr(ModelServices, "detect", lambda *a: detection)
    with DagRepository(LocalArtifactStore(tmp_path / "store"), tmp_path / "runtime") as repo:
        execution = NodeEditorExecution(DagEngine(repo, AdapterRegistry()))
        editor = DraftEditor(tmp_path / "editor", execution=execution)
        server = create_editor_server(editor, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def post(path, body, origin=None):
            headers = {"Content-Type": "application/json"}
            if origin:
                headers["Origin"] = origin
            return json.load(
                urlopen(
                    Request(
                        f"http://127.0.0.1:{server.server_port}" + path,
                        data=json.dumps(body).encode(),
                        headers=headers,
                    )
                )
            )

        try:
            reply = post("/api/model-services/detect", {"endpoint": detection["endpoint"]})
            added = post(
                "/api/model-services",
                {"endpoint": reply["endpoint"], "descriptor_digest": reply["descriptor_digest"]},
            )
            assert added["catalog"]["model_services"][0]["backend"] == added["backend"]
            assert added["catalog"]["model_services"][0]["frame_id"] == "model-native"
            assert added["catalog"]["model_services"][0]["up_axis"] == "+Y"
            assert added["catalog"]["model_services"][0]["unit"] == "relative_unit"
            with pytest.raises(HTTPError) as error:
                post(
                    "/api/model-services",
                    {"endpoint": reply["endpoint"], "descriptor": reply["descriptor"]},
                )
            assert error.value.code == 400
            with pytest.raises(HTTPError) as error:
                post(
                    "/api/model-services/detect",
                    {"endpoint": reply["endpoint"]},
                    "http://untrusted.test",
                )
            assert error.value.code == 403
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            execution.close()


def test_post_replace_failure_restores_on_reopen(tmp_path, monkeypatch):
    detection = detected()
    monkeypatch.setattr(ModelServices, "detect", lambda *a: detection)
    with DagRepository(LocalArtifactStore(tmp_path / "store"), tmp_path / "runtime") as repo:
        execution = NodeEditorExecution(DagEngine(repo, AdapterRegistry()))
        editor = DraftEditor(tmp_path / "editor", execution=execution)
        path = editor.model_services.path

        def fail_after_replace(operation, directory):
            if operation == "directory_fsync" and path.exists():
                raise OSError("directory fsync failed after replace")

        editor.model_services.io.failpoint = fail_after_replace
        with pytest.raises(OSError, match="after replace"):
            editor.add_model_service(
                {
                    "endpoint": detection["endpoint"],
                    "descriptor_digest": detection["descriptor_digest"],
                }
            )
        assert execution.engine.registry.backend_catalog() == []
        restored = ModelServices(tmp_path / "editor")
        registry = AdapterRegistry()
        restored.restore(registry)
        assert len(registry.backend_catalog()) == 1
        assert len(restored.summaries()) == 1
        execution.close()


def test_active_command_refuses_install_without_persisting(tmp_path, monkeypatch):
    import threading

    detection = detected()
    monkeypatch.setattr(ModelServices, "detect", lambda *a: detection)
    with DagRepository(LocalArtifactStore(tmp_path / "store"), tmp_path / "runtime") as repo:
        execution = NodeEditorExecution(DagEngine(repo, AdapterRegistry()))
        editor = DraftEditor(tmp_path / "editor", execution=execution)
        stop = threading.Event()
        worker = threading.Thread(target=stop.wait)
        worker.start()
        execution._worker = worker
        try:
            with pytest.raises(ValueError, match="still executing"):
                editor.add_model_service(
                    {
                        "endpoint": detection["endpoint"],
                        "descriptor_digest": detection["descriptor_digest"],
                    }
                )
            assert not editor.model_services.path.exists()
            assert execution.engine.registry.backend_catalog() == []
        finally:
            stop.set()
            worker.join()
            execution.close()


def test_install_each_known_capability_creates_independent_backend(tmp_path, monkeypatch):
    from assets_generator.model_service_descriptor import validate_descriptor

    base = detected("Multi", "c")
    flat = base["descriptor"]
    multi = {
        key: flat[key] for key in ("schema_version", "display_name", "service_id", "backend_digest")
    }
    multi["capabilities"] = [
        {
            "capability_id": "mesh_fast",
            "operator": "shape_generation@1",
            "transport": "remote_jobs@1",
            "parameter_schema": flat["parameter_schema"],
            "defaults": flat["defaults"],
        },
        {
            "capability_id": "mesh_quality",
            "operator": "shape_generation@1",
            "transport": "remote_jobs@1",
            "parameter_schema": {
                "type": "object",
                "properties": {"steps": {"type": "integer", "minimum": 1}},
            },
            "defaults": {"steps": 20},
        },
    ]
    normalized = validate_descriptor(multi)
    detection = {
        "endpoint": base["endpoint"],
        "descriptor": normalized,
        "descriptor_digest": sha256_bytes(canonical_json_bytes(normalized)),
    }
    monkeypatch.setattr(ModelServices, "detect", lambda *args: detection)
    with DagRepository(LocalArtifactStore(tmp_path / "store"), tmp_path / "runtime") as repo:
        execution = NodeEditorExecution(DagEngine(repo, AdapterRegistry()))
        editor = DraftEditor(tmp_path / "editor", execution=execution)
        first = editor.add_model_service(
            {
                "endpoint": detection["endpoint"],
                "descriptor_digest": detection["descriptor_digest"],
                "capability_id": "mesh_fast",
            }
        )
        second = editor.add_model_service(
            {
                "endpoint": detection["endpoint"],
                "descriptor_digest": detection["descriptor_digest"],
                "capability_id": "mesh_quality",
            }
        )
        assert first["backend"] != second["backend"]
        assert {item["capability_id"] for item in editor.catalog()["model_services"]} == {
            "mesh_fast",
            "mesh_quality",
        }
        assert len(execution.engine.registry.backend_catalog()) == 2
        execution.close()


@pytest.mark.parametrize(
    "operator,transport,installable",
    [
        ("shape_generation@1", "remote_jobs@1", True),
        ("text_segmentation@1", "remote_jobs@1", False),
        ("shape_generation@1", "other@1", False),
    ],
)
def test_detection_reports_executable_adapter_boundary(
    tmp_path, monkeypatch, operator, transport, installable
):
    current = detected()
    current["descriptor"]["operator"] = operator
    current["descriptor"]["transport"] = transport
    current["descriptor"]["capabilities"] = [
        {
            key: current["descriptor"][key]
            for key in ("operator", "transport", "parameter_schema", "defaults")
        }
    ]
    current["descriptor"]["capabilities"][0]["capability_id"] = "selected"
    monkeypatch.setattr(ModelServices, "detect", lambda *args: current)
    with DagRepository(LocalArtifactStore(tmp_path / "store"), tmp_path / "runtime") as repo:
        execution = NodeEditorExecution(DagEngine(repo, AdapterRegistry()))
        try:
            editor = DraftEditor(tmp_path / "editor", execution=execution)
            result = editor.detect_model_service({"endpoint": current["endpoint"]})
            status = result["capability_availability"]["selected"]
            assert status["installable"] is installable
            assert bool(status["reason"]) is not installable
            assert not editor.model_services.path.exists()
        finally:
            execution.close()

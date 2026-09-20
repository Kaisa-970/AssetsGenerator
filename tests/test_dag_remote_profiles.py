from pathlib import Path

import pytest
import yaml

from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_remote_profiles import register_remote_shape_profiles
from assets_generator.node_editor import DraftEditor
from assets_generator.pipeline import (
    _pipeline_from_raw,
    compile_pipeline,
    load_default_operator_specs,
)


def configuration():
    return {
        "default_profile": "first",
        "profiles": {
            name: {
                "endpoint": f"http://127.0.0.1:{port}",
                "service_id": name,
                "backend_digest": "sha256:" + char * 64,
            }
            for name, port, char in (("first", 8770, "a"), ("second", 8771, "b"))
        },
    }


def test_services_bind_per_node_without_contacting_server(tmp_path):
    registry = AdapterRegistry()
    register_remote_shape_profiles(registry, configuration())
    raw = yaml.safe_load(Path("pipelines/remote_shape_asset_v1.yaml").read_text())
    raw["nodes"]["shape"]["backend"] = "second"
    raw["nodes"]["another_shape"] = {
        "operator": "shape_generation@1",
        "backend": "first",
        "inputs": {"image": "pipeline.inputs.image"},
    }
    plan = registry.bind_plan(
        compile_pipeline(
            _pipeline_from_raw(raw), load_default_operator_specs(), require_explicit_joins=True
        )
    )
    assert plan.bindings["shape"].parameters["service_id"] == "second"
    assert plan.bindings["another_shape"].parameters["service_id"] == "first"
    assert len(DraftEditor(tmp_path, adapters=registry).catalog()["backends"]) == 2
    raw["nodes"]["shape"]["parameters"] = {"remote_endpoint": "http://127.0.0.1:9999"}
    assert not DraftEditor(tmp_path, adapters=registry).compile(raw)["ok"]


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"default_profile": "absent", "profiles": {}},
        {"default_profile": "one", "profiles": {"one": {"endpoint": "http://localhost"}}},
    ],
)
def test_invalid_config_rejected(config):
    with pytest.raises(ValueError):
        register_remote_shape_profiles(AdapterRegistry(), config)


def test_remote_only_editor_starts_without_model_environment(tmp_path, monkeypatch):
    import json

    from assets_generator import node_editor

    path = tmp_path / "remote.json"
    path.write_text(json.dumps(configuration()))

    class Server:
        server_port = 0

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    def create(editor, port):
        catalog = editor.catalog()
        assert catalog["execution_enabled"]
        assert len(catalog["adapters"]) == 6
        raw = yaml.safe_load(Path("pipelines/remote_shape_asset_v1.yaml").read_text())
        compiled = editor.compile(raw)
        assert compiled["ok"]
        assert compiled["execution_ready"]
        return Server()

    monkeypatch.setattr(node_editor, "create_editor_server", create)
    node_editor.serve_editor(
        tmp_path / "editor", 0, None, [], store=tmp_path / "store", remote_config=path
    )


def test_editor_rgba_upload_start_and_empty_foreground_rejection(tmp_path):
    import io

    from PIL import Image
    from test_node_editor_execution import wait
    from test_remote_http import request
    from test_remote_service_http import serve

    from assets_generator.artifact_store import LocalArtifactStore
    from assets_generator.dag_engine import DagEngine
    from assets_generator.dag_persistence import DagRepository
    from assets_generator.models import ArtifactRef
    from assets_generator.node_editor_execution import NodeEditorExecution

    with serve(tmp_path / "service.sqlite") as (remote, client, _):
        identity = request().identity
        registry = AdapterRegistry()
        register_remote_shape_profiles(
            registry,
            {
                "default_profile": "test",
                "profiles": {
                    "test": {
                        "endpoint": client.endpoint,
                        "service_id": identity.service_id,
                        "backend_digest": identity.backend_digest,
                    }
                },
            },
        )
        with DagRepository(LocalArtifactStore(tmp_path / "store"), tmp_path / "core") as repo:
            execution = NodeEditorExecution(DagEngine(repo, registry))
            try:
                graph = yaml.safe_load(Path("pipelines/remote_shape_asset_v1.yaml").read_text())
                buffer = io.BytesIO()
                Image.new("RGBA", (2, 2), (255, 0, 0, 100)).save(buffer, format="PNG")
                uploaded = execution.upload_image(buffer.getvalue(), rgba=True)
                run = execution.start(graph, image_ref=uploaded["image_ref"])
                state = wait(execution, run["run"]["run_id"])
                assert state["run"]["dag"]["node_states"]["shape"]["status"] == "running"
                assert (
                    repo.store.blob_path(ArtifactRef(**uploaded["image_ref"])).read_bytes()
                    == buffer.getvalue()
                )
                buffer = io.BytesIO()
                Image.new("RGBA", (2, 2), (255, 0, 0, 0)).save(buffer, format="PNG")
                with pytest.raises(ValueError, match="foreground"):
                    execution.upload_image(buffer.getvalue(), rgba=True)
                bad = repo.store.persist_bytes(
                    buffer.getvalue(),
                    kind="rgba_image",
                    schema_name="png",
                    schema_version="1.0",
                    identity_metadata={"media_type": "image/png", "channel_layout": "RGBA"},
                )
                with pytest.raises(ValueError, match="foreground"):
                    execution.start(graph, image_ref={"artifact_id": bad.artifact_id})
            finally:
                execution.close()

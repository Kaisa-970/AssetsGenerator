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
        assert len(catalog["adapters"]) == 5
        raw = yaml.safe_load(Path("pipelines/remote_shape_asset_v1.yaml").read_text())
        compiled = editor.compile(raw)
        assert compiled["ok"]
        assert not compiled["execution_ready"]
        assert "rgb_image" in compiled["execution_reason"]
        return Server()

    monkeypatch.setattr(node_editor, "create_editor_server", create)
    node_editor.serve_editor(
        tmp_path / "editor", 0, None, [], store=tmp_path / "store", remote_config=path
    )

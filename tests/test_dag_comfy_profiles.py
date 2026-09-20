import json
from pathlib import Path

import pytest
from test_comfy_profile import profile

from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_comfy_profiles import register_comfy_profiles
from assets_generator.pipeline import compile_pipeline, load_default_operator_specs, load_pipeline


def test_config_binds_two_instances_to_distinct_profiles(tmp_path):
    raw = profile()
    (tmp_path / "first.json").write_text(json.dumps(raw))
    raw["defaults"]["seed"] = 9
    (tmp_path / "second.json").write_text(json.dumps(raw))
    config = {
        "default_profile": "comfy_first",
        "profiles": {
            "comfy_first": {"endpoint": "http://127.0.0.1:8771", "profile": "first.json"},
            "comfy_second": {"endpoint": "http://127.0.0.1:8772", "profile": "second.json"},
        },
    }
    registry = AdapterRegistry()
    register_comfy_profiles(registry, config, base=tmp_path)
    plan = registry.bind_plan(
        compile_pipeline(
            load_pipeline(Path("pipelines/comfy_image_chain_v1.yaml")),
            load_default_operator_specs(),
            require_explicit_joins=True,
        )
    )
    first, second = plan.bindings["first"], plan.bindings["second"]
    assert first.parameters["seed"] == 1
    assert second.parameters["seed"] == 9
    assert first.parameters["backend_digest"] != second.parameters["backend_digest"]


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"default_profile": "absent", "profiles": {}},
        {"default_profile": "one", "profiles": {"one": {"endpoint": "x"}}},
    ],
)
def test_invalid_config_rejected(tmp_path, raw):
    with pytest.raises(ValueError):
        register_comfy_profiles(AdapterRegistry(), raw, base=tmp_path)


def test_editor_starts_with_comfy_only_catalog(tmp_path, monkeypatch):
    from assets_generator import node_editor

    (tmp_path / "workflow.json").write_text(json.dumps(profile()))
    config = tmp_path / "comfy.json"
    config.write_text(
        json.dumps(
            {
                "default_profile": "comfy_first",
                "profiles": {
                    "comfy_first": {"endpoint": "http://127.0.0.1:8771", "profile": "workflow.json"}
                },
            }
        )
    )

    class Server:
        server_port = 0

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    def create(editor, port):
        assert {entry["name"] for entry in editor.catalog()["adapters"]} == {
            "comfy_image",
            "encode_png",
            "resize_image",
            "apply_binary_mask",
        }
        assert editor.execution is not None
        return Server()

    monkeypatch.setattr(node_editor, "create_editor_server", create)
    node_editor.serve_editor(
        tmp_path / "editor", 0, None, [], comfy_config=config, store=tmp_path / "store"
    )

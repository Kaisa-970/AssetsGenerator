import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from assets_generator.node_editor import DraftEditor, create_editor_server


def fixture(tmp_path):
    return DraftEditor(
        tmp_path / "drafts",
        Path("examples/dag-operators.yaml"),
        [Path("examples/dag-diamond.yaml")],
    )


def test_compile_roundtrip_and_layout_identity(tmp_path):
    editor = fixture(tmp_path)
    pipeline = editor.catalog()["templates"][0]["pipeline"]
    expected = editor.compile(pipeline)
    assert expected["ok"] and not expected["execution_ready"]
    editor.save("diamond", {"pipeline": pipeline, "layout": {"A": {"x": 100, "y": 200}}})
    saved = json.loads(editor.path("diamond").read_text())
    assert editor.compile(saved["pipeline"])["plan"] == expected["plan"]
    pipeline["nodes"]["B"]["inputs"]["source"] = "D.outputs.result"
    assert not editor.compile(pipeline)["ok"]
    for name in ["../escape", "foo/bar", "", "x.json"]:
        with pytest.raises(ValueError):
            editor.save(name, saved)


def test_http_compile_save_load_and_origin(tmp_path):
    editor = fixture(tmp_path)
    server = create_editor_server(editor, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/api/catalog") as response:
            catalog = json.load(response)
        draft = {"pipeline": catalog["templates"][0]["pipeline"], "layout": {}}

        def send(path, body, method="POST", origin=None):
            headers = {"Content-Type": "application/json"}
            if origin:
                headers["Origin"] = origin
            return urlopen(Request(base + path, json.dumps(body).encode(), headers, method=method))

        with send("/api/compile", draft) as response:
            assert json.load(response)["ok"]
        with send("/api/drafts/demo", draft, "PUT") as response:
            assert json.load(response)["saved"] == "demo"
        with urlopen(base + "/api/drafts/demo") as response:
            assert json.load(response) == draft
        with pytest.raises(HTTPError) as caught:
            send("/api/drafts/demo", draft, "PUT", "http://evil.test")
        assert caught.value.code == 403
        with send("/api/compile", {"pipeline": {"nodes": []}}) as response:
            assert not json.load(response)["ok"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_catalog_and_compile_use_optional_registered_adapter(tmp_path):
    from assets_generator.dag_adapters import AdapterRegistry, AdapterSpec, NodeExecutionResult

    class Copy:
        spec = AdapterSpec(
            "editor_copy",
            "1",
            ("demo_copy@1",),
            {"type": "object", "properties": {"count": {"type": "integer", "minimum": 1}}},
            {"count": 1},
        )

        def execute(self, context):
            pytest.fail("editor cannot execute")
            return NodeExecutionResult()

    registry = AdapterRegistry()
    registry.register(Copy())
    editor = DraftEditor(tmp_path, Path("examples/dag-operators.yaml"), adapters=registry)
    raw = {
        "pipeline": "test",
        "version": "1",
        "inputs": {
            "source": {
                "kind": "quality_evidence",
                "carriers": ["artifact_ref"],
                "schema_name": "DemoText",
                "schema_version": "1",
            }
        },
        "nodes": {
            "copy": {
                "operator": "demo_copy@1",
                "adapter": "editor_copy@1",
                "inputs": {"source": "pipeline.inputs.source"},
                "parameters": {"count": 0},
            }
        },
    }
    assert editor.catalog()["adapters"][0]["defaults"] == {"count": 1}
    assert not editor.compile(raw)["ok"]
    raw["nodes"]["copy"]["parameters"]["count"] = 1
    result = editor.compile(raw)
    assert result["ok"], result
    assert result["bound_plan"] and not result["execution_ready"]


def test_diagnostic_locates_bad_input_port(tmp_path):
    editor = fixture(tmp_path)
    raw = editor.catalog()["templates"][0]["pipeline"]
    raw["nodes"]["B"]["inputs"]["source"] = "A.outputs.missing"
    diagnostic = editor.compile(raw)["diagnostics"][0]
    assert diagnostic["node_id"] == "B"
    assert diagnostic["port"] == "source"

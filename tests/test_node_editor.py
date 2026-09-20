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


def test_http_execution_routes_and_strict_requests(tmp_path):
    class Execution:
        def start(self, pipeline, image_path):
            assert pipeline == {"pipeline": "draft"}
            assert image_path == "/tmp/input.png"
            return {"run": {"run_id": "dag_example"}, "busy": True, "error": None}

        def snapshot(self, run_id):
            assert run_id == "dag_example"
            return {"run": {"run_id": run_id}, "busy": False, "error": None}

        def list_runs(self):
            return [{"run_id": "dag_example", "status": "succeeded"}]

        def resume(self, run_id, expected_revision):
            assert run_id == "dag_example" and expected_revision == 3
            return self.snapshot(run_id)

        def output(self, run_id, node, port):
            from assets_generator.workbench_http import OutputPayload

            assert (run_id, node, port) == ("dag_example", "generate", "glb")
            return OutputPayload(b"glTF", "model/gltf-binary")

    editor = fixture(tmp_path)
    editor.execution = Execution()
    server = create_editor_server(editor, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def post(path, body, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        return urlopen(Request(base + path, json.dumps(body).encode(), headers, method="POST"))

    try:
        with post(
            "/api/runs", {"pipeline": {"pipeline": "draft"}, "image_path": "/tmp/input.png"}
        ) as response:
            assert response.status == 202
            assert json.load(response)["run"]["run_id"] == "dag_example"
        with urlopen(base + "/api/runs") as response:
            assert len(json.load(response)["runs"]) == 1
        with urlopen(base + "/api/runs/dag_example") as response:
            assert not json.load(response)["busy"]
        with post("/api/runs/dag_example/resume", {"expected_revision": 3}) as response:
            assert response.status == 202
        with urlopen(base + "/api/runs/dag_example/outputs/generate/glb") as response:
            assert response.headers["Content-Type"] == "model/gltf-binary"
            assert response.read() == b"glTF"
        for body in (
            {"pipeline": {}},
            {"pipeline": {}, "image_path": "/tmp/input.png", "extra": True},
        ):
            with pytest.raises(HTTPError) as caught:
                post("/api/runs", body)
            assert caught.value.code == 400
        for invalid_node in ([], {}, None, 1, ""):
            with pytest.raises(HTTPError) as caught:
                post("/api/runs/dag_example/review", {"node_id": invalid_node})
            assert caught.value.code == 400
        with pytest.raises(HTTPError) as caught:
            post("/api/runs", {}, "http://evil.test")
        assert caught.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

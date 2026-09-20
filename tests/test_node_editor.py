import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import yaml

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
    # A second instance named like the parameter must not confuse location.
    raw["nodes"]["count"] = {**raw["nodes"]["copy"], "parameters": {"count": 1}}
    failure = editor.compile(raw)
    assert not failure["ok"]
    assert failure["diagnostics"][0]["node_id"] == "copy"
    assert "parameters.count" in failure["diagnostics"][0]["message"]
    assert "port" not in failure["diagnostics"][0]
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
        def import_observations(self, images):
            assert images == [{"artifact_id": "one"}, {"artifact_id": "two"}]
            return {"observations_ref": {"artifact_id": "bundle"}}

        def upload_image(self, data, *, rgba=False):
            assert data == (b"rgba bytes" if rgba else b"image bytes")
            return {"image_ref": {"artifact_id": "sha256:" + "a" * 64}}

        def start(
            self,
            pipeline,
            image_path=None,
            *,
            image_ref=None,
            observations_ref=None,
            idempotency_key=None,
        ):
            assert idempotency_key in (None, "request-one")
            assert pipeline == {"pipeline": "draft"}
            if observations_ref is not None:
                assert observations_ref == {"artifact_id": "sha256:" + "b" * 64}
            elif image_ref is None:
                assert image_path == "/tmp/input.png"
            else:
                assert image_path is None
                assert image_ref == {"artifact_id": "sha256:" + "a" * 64}
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
        with urlopen(
            Request(
                base + "/api/inputs/image",
                b"image bytes",
                {"Content-Type": "application/octet-stream"},
                method="POST",
            )
        ) as response:
            assert response.status == 201
            assert json.load(response)["image_ref"]["artifact_id"] == "sha256:" + "a" * 64
        with pytest.raises(HTTPError) as caught:
            urlopen(
                Request(
                    base + "/api/inputs/image",
                    b"image bytes",
                    {"Content-Type": "application/octet-stream", "Origin": "http://evil.test"},
                    method="POST",
                )
            )
        assert caught.value.code == 403
        with urlopen(
            Request(
                base + "/api/inputs/rgba",
                b"rgba bytes",
                {"Content-Type": "application/octet-stream"},
                method="POST",
            )
        ) as response:
            assert response.status == 201
        with pytest.raises(HTTPError) as caught:
            post("/api/inputs/image", {})
        assert caught.value.code == 400
        with post(
            "/api/runs", {"pipeline": {"pipeline": "draft"}, "image_path": "/tmp/input.png"}
        ) as response:
            assert response.status == 202
            assert json.load(response)["run"]["run_id"] == "dag_example"
        with post(
            "/api/runs",
            {
                "pipeline": {"pipeline": "draft"},
                "image_ref": {"artifact_id": "sha256:" + "a" * 64},
            },
        ) as response:
            assert response.status == 202
        with post(
            "/api/runs",
            {
                "pipeline": {"pipeline": "draft"},
                "image_path": "/tmp/input.png",
                "idempotency_key": "request-one",
            },
        ) as response:
            assert response.status == 202
        with post(
            "/api/runs",
            {
                "pipeline": {"pipeline": "draft"},
                "observations_ref": {"artifact_id": "sha256:" + "b" * 64},
            },
        ) as response:
            assert response.status == 202
        with post(
            "/api/inputs/observations", {"images": [{"artifact_id": "one"}, {"artifact_id": "two"}]}
        ) as response:
            assert response.status == 201
            assert json.load(response)["observations_ref"]["artifact_id"] == "bundle"
        with pytest.raises(HTTPError):
            post("/api/inputs/observations", {"images": [], "extra": True})
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


def test_multi_view_service_configuration(tmp_path, monkeypatch):
    from test_multi_view_workflow import ContractGeometryFrontend, ContractReconstruction, _plan

    from assets_generator import multi_view_profiles, node_editor
    from assets_generator.dag_multi_view import MultiViewProfile

    config = tmp_path / "multi.json"
    config.write_text(
        json.dumps({"default_profile": "local", "profiles": {"local": {"fixture": True}}})
    )
    loaded = []

    def load(raw):
        loaded.append(raw)
        return MultiViewProfile(
            _plan(ContractGeometryFrontend(), ContractReconstruction()),
            {"fixture": True},
            test_only=True,
        )

    monkeypatch.setattr(multi_view_profiles, "load_multi_view_profile", load)

    class Server:
        server_port = 0

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    def create(editor, port):
        assert editor.execution is not None
        assert len(editor.catalog()["backends"]) == 3
        graph = yaml.safe_load(Path("examples/dag-multi-view-asset.yaml").read_text())
        assert editor.compile(graph)["execution_ready"]
        return Server()

    monkeypatch.setattr(node_editor, "create_editor_server", create)
    node_editor.serve_editor(
        tmp_path / "editor",
        0,
        Path("examples/dag-multi-view-operators.yaml"),
        [],
        store=tmp_path / "store",
        multi_view_config=config,
    )
    assert loaded == [{"fixture": True}]


def test_proposal_only_editor_registers_no_local_shape(tmp_path, monkeypatch):
    from test_workbench_engine import fixture_engine

    from assets_generator import node_editor, workbench_profiles
    from assets_generator.workbench_profiles import ProposalProfile

    _, _, combined = fixture_engine(tmp_path)
    config = tmp_path / "sam.json"
    config.write_text('{"profiles": {"sam": {}}}')
    monkeypatch.setattr(
        workbench_profiles,
        "load_proposal_profiles",
        lambda *args, **kwargs: {
            "sam": ProposalProfile(
                "sam", combined.proposer, combined.proposal_identity, test_only=True
            )
        },
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("local shape config must not load")

    monkeypatch.setattr(workbench_profiles, "load_profiles", forbidden)

    class Server:
        server_port = 0

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    def create(editor, port):
        assert {entry["name"] for entry in editor.catalog()["adapters"]} == {
            "image_proposals",
            "image_mask_selection",
            "encode_png",
            "resize_image",
            "apply_binary_mask",
        }
        return Server()

    monkeypatch.setattr(node_editor, "create_editor_server", create)
    node_editor.serve_editor(
        tmp_path / "editor",
        0,
        None,
        [],
        proposal_config=config,
        profile="sam",
        store=tmp_path / "store",
    )


def test_proposal_configuration_requires_profile_before_loading(tmp_path, monkeypatch):
    from assets_generator import node_editor, workbench_profiles

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid configuration must fail before loading models")

    monkeypatch.setattr(workbench_profiles, "load_proposal_profiles", forbidden)
    with pytest.raises(ValueError, match="--proposal-config"):
        node_editor.serve_editor(
            tmp_path / "editor",
            0,
            None,
            [],
            proposal_config=tmp_path / "sam.json",
            store=tmp_path / "store",
        )
    with pytest.raises(ValueError, match="either image config or proposal config"):
        node_editor.serve_editor(
            tmp_path / "editor",
            0,
            None,
            [],
            config=tmp_path / "image.json",
            proposal_config=tmp_path / "sam.json",
            profile="sam",
            store=tmp_path / "store",
        )


def test_execution_editor_uses_authoritative_custom_contracts_and_relations(tmp_path):
    from assets_generator.artifact_store import LocalArtifactStore
    from assets_generator.dag_adapters import AdapterRegistry
    from assets_generator.dag_engine import DagEngine
    from assets_generator.dag_persistence import DagRepository
    from assets_generator.node_editor_execution import NodeEditorExecution
    from assets_generator.pipeline import load_operator_specs
    from assets_generator.relations import default_relation_registry

    specs = load_operator_specs(Path("examples/dag-operators.yaml"))
    relations = default_relation_registry()
    with DagRepository(LocalArtifactStore(tmp_path / "store"), tmp_path / "runtime") as repo:
        service = NodeEditorExecution(
            DagEngine(repo, AdapterRegistry(), relations), specs=specs, relations=relations
        )
        try:
            editor = DraftEditor(tmp_path / "drafts", execution=service)
            assert editor.specs is service.specs
            assert editor.relations is relations
            assert set(editor.catalog()["operators"]) == set(specs)
            assert (
                DraftEditor(
                    tmp_path / "same", Path("examples/dag-operators.yaml"), execution=service
                ).specs
                is specs
            )
            from assets_generator.contracts import ContractError

            with pytest.raises(ContractError, match="differ"):
                DraftEditor(
                    tmp_path / "conflict", Path("pipelines/operators-v1.yaml"), execution=service
                )
        finally:
            service.close()


def test_remote_only_editor_runs_cpu_utilities_without_contacting_service(tmp_path, monkeypatch):
    from io import BytesIO

    from PIL import Image
    from test_node_editor_execution import wait

    from assets_generator import node_editor

    config = tmp_path / "remote.json"
    config.write_text(
        json.dumps(
            {
                "default_profile": "offline",
                "profiles": {
                    "offline": {
                        "endpoint": "http://127.0.0.1:1",
                        "service_id": "offline",
                        "backend_digest": "sha256:" + "a" * 64,
                    }
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
        graph = yaml.safe_load(Path("examples/cpu-image-resize.yaml").read_text())
        assert editor.compile(graph)["execution_ready"]
        image = BytesIO()
        Image.new("RGB", (4, 2), "red").save(image, format="PNG")
        service = editor.execution
        uploaded = service.upload_image(image.getvalue())
        created = service.start(graph, image_ref=uploaded["image_ref"])
        completed = wait(service, created["run"]["run_id"])
        assert completed["run"]["status"] == "succeeded"
        assert all(len(s["attempts"]) == 1 for s in completed["run"]["dag"]["node_states"].values())
        payload = service.output(created["run"]["run_id"], "thumbnail", "image")
        with Image.open(BytesIO(payload.data)) as output:
            assert output.size == (128, 128)
        return Server()

    monkeypatch.setattr(node_editor, "create_editor_server", create)
    node_editor.serve_editor(
        tmp_path / "editor", 0, None, [], remote_config=config, store=tmp_path / "store"
    )


def test_http_input_refs_requires_complete_port_mapping(tmp_path):
    from http.client import HTTPConnection
    from io import BytesIO

    from PIL import Image

    from assets_generator.artifact_store import LocalArtifactStore
    from assets_generator.dag_adapters import AdapterRegistry
    from assets_generator.dag_engine import DagEngine
    from assets_generator.dag_image_encoding import EncodePngAdapter
    from assets_generator.dag_persistence import DagRepository
    from assets_generator.node_editor import DraftEditor, create_editor_server
    from assets_generator.node_editor_execution import NodeEditorExecution

    graph = {
        "pipeline": "http_multi",
        "version": "1",
        "inputs": {
            "image": {
                "kind": "rgb_image",
                "carriers": ["artifact_ref"],
                "schema_name": "raster_image",
                "schema_version": "1.0",
            },
            "other": {
                "kind": "rgb_image",
                "carriers": ["artifact_ref"],
                "schema_name": "raster_image",
                "schema_version": "1.0",
            },
        },
        "nodes": {
            "encode": {"operator": "encode_png@1", "inputs": {"image": "pipeline.inputs.image"}}
        },
    }
    store = LocalArtifactStore(tmp_path / "store")
    registry = AdapterRegistry()
    registry.register(EncodePngAdapter())
    with DagRepository(store, tmp_path / "runtime") as repo:
        execution = NodeEditorExecution(DagEngine(repo, registry))
        server = create_editor_server(DraftEditor(tmp_path / "drafts", execution=execution), 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            image = BytesIO()
            Image.new("RGB", (2, 2), "red").save(image, format="PNG")
            refs = [execution.upload_image(image.getvalue())["image_ref"] for _ in range(2)]
            conn = HTTPConnection("127.0.0.1", server.server_port)
            body = json.dumps(
                {
                    "pipeline": graph,
                    "input_refs": {"image": refs[0], "other": refs[1]},
                    "idempotency_key": "http-two",
                }
            )
            conn.request("POST", "/api/runs", body, {"Content-Type": "application/json"})
            response = conn.getresponse()
            assert response.status == 202
            run = json.loads(response.read())
            assert (
                run["run"]["dag"]["named_actual_inputs"]["other"]["artifact_id"]
                == refs[1]["artifact_id"]
            )
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            execution.close()


def test_http_mask_upload_returns_binary_mask_reference(tmp_path):
    import io
    from http.client import HTTPConnection

    from PIL import Image

    from assets_generator.artifact_store import LocalArtifactStore
    from assets_generator.dag_adapters import AdapterRegistry
    from assets_generator.dag_engine import DagEngine
    from assets_generator.dag_persistence import DagRepository
    from assets_generator.node_editor import DraftEditor, create_editor_server
    from assets_generator.node_editor_execution import NodeEditorExecution

    with DagRepository(LocalArtifactStore(tmp_path / "store"), tmp_path / "runtime") as repo:
        execution = NodeEditorExecution(DagEngine(repo, AdapterRegistry()))
        server = create_editor_server(DraftEditor(tmp_path / "drafts", execution=execution), 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            data = io.BytesIO()
            Image.frombytes("L", (2, 2), bytes([255, 0, 0, 255])).save(data, format="PNG")
            conn = HTTPConnection("127.0.0.1", server.server_port)
            conn.request(
                "POST",
                "/api/inputs/mask",
                data.getvalue(),
                {"Content-Type": "application/octet-stream"},
            )
            response = conn.getresponse()
            assert response.status == 201
            body = json.loads(response.read())
            assert body["mask_ref"]["artifact_id"].startswith("sha256:")
            conn.close()
        finally:
            server.shutdown()
            thread.join()
            execution.close()

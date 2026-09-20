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
        assert {entry["name"] for entry in catalog["adapters"]} == {
            "remote_shape",
            "selection_prepare",
            "canonicalize_shape",
            "geometry_validation",
            "shape_asset_assembly",
            "asset_export",
            "encode_png",
            "resize_image",
            "apply_binary_mask",
        }
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


def test_local_selection_and_remote_shape_profiles_bind_composed_template(tmp_path):
    from test_workbench_engine import fixture_engine

    from assets_generator.dag_profiles import image_adapter_registry

    # The local profile supplies proposal/selection adapters; remote profile owns shape generation.
    _, _, local_profile = fixture_engine(tmp_path)
    registry = image_adapter_registry({"local": local_profile}, "local")
    register_remote_shape_profiles(registry, configuration())
    raw = yaml.safe_load(Path("pipelines/remote_selected_image_asset_v1.yaml").read_text())
    raw["nodes"]["shape"]["backend"] = "second"
    plan = registry.bind_plan(
        compile_pipeline(
            _pipeline_from_raw(raw), load_default_operator_specs(), require_explicit_joins=True
        )
    )
    assert plan.bindings["candidates"].adapter == "image_proposals@1"
    assert plan.bindings["shape"].parameters["service_id"] == "second"


@pytest.mark.parametrize("full_release", [False, True])
def test_two_http_services_keep_node_jobs_and_offline_results_separate(tmp_path, full_release):
    import io
    from contextlib import ExitStack

    from PIL import Image
    from test_remote_service_http import serve
    from test_remote_shape_service import Backend

    from assets_generator.artifact_store import LocalArtifactStore
    from assets_generator.dag_engine import DagEngine
    from assets_generator.dag_persistence import DagRepository
    from assets_generator.remote_protocol import RemoteIdentity
    from assets_generator.remote_service_worker import execute_service_job
    from assets_generator.remote_shape_service import ShapeServiceHandler

    store = LocalArtifactStore(tmp_path / "store")
    data = io.BytesIO()
    Image.new("RGBA", (2, 2), "red").save(data, format="PNG")
    image = store.persist_bytes(
        data.getvalue(),
        kind="rgba_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGBA"},
    )
    with ExitStack() as stack:
        services, configured = {}, {}
        for name, char in (("first", "a"), ("second", "b")):
            identity = RemoteIdentity(name, "sha256:" + char * 64)
            service, client, _ = stack.enter_context(
                serve(tmp_path / f"{name}.sqlite", identity=identity)
            )
            services[name] = (service, identity)
            configured[name] = {
                "endpoint": client.endpoint,
                "service_id": name,
                "backend_digest": identity.backend_digest,
            }
        registry = AdapterRegistry()
        register_remote_shape_profiles(
            registry, {"default_profile": "first", "profiles": configured}
        )
        graph = {
            "pipeline": "two_remote_shapes",
            "version": "1",
            "inputs": {"image": {"kind": "rgba_image", "carriers": ["artifact_ref"]}},
            "nodes": {
                name: {
                    "operator": "shape_generation@1",
                    "adapter": "remote_shape@1",
                    "backend": name,
                    "inputs": {"image": "pipeline.inputs.image"},
                }
                for name in services
            },
        }
        if full_release:
            graph = yaml.safe_load(Path("examples/remote-shape-compare.yaml").read_text())
            for name in services:
                graph["nodes"][name + "_shape"]["backend"] = name

        def node_id(name):
            return name + "_shape" if full_release else name

        plan = registry.bind_plan(
            compile_pipeline(
                _pipeline_from_raw(graph),
                load_default_operator_specs(),
                require_explicit_joins=True,
            )
        )
        with DagRepository(store, tmp_path / "core") as repo:
            engine = DagEngine(repo, registry)
            run = engine.drain(engine.create(plan, {"image": image}).run_id)
            keys = []
            for name, (service, identity) in services.items():
                attempt = run.dag.node_states[node_id(name)].current()
                assert attempt.resolved_inputs["image"] == image
                assert attempt.remote_binding.service_id == name
                key = attempt.remote_binding.submission_key
                keys.append(key)
                assert len(service.list_jobs()["jobs"]) == 1
                request = service.request_for(key)
                assert request.identity == identity
                handler = ShapeServiceHandler(
                    identity, tmp_path / name, Backend, lambda identity=identity: identity
                )
                assert execute_service_job(service, request, handler).state == "succeeded"
                run = engine.drain(run.run_id)
                if name == "first":
                    assert run.dag.node_states[node_id("first")].status == "succeeded"
                    assert run.dag.node_states[node_id("second")].status == "running"
            assert len(set(keys)) == 2
            assert run.status == "succeeded"
            if full_release:
                from assets_generator.models import ArtifactRef

                assets, releases = [], []
                for name in services:
                    assembled = run.dag.node_states[name + "_assemble"].current()
                    published = run.dag.node_states[name + "_publish"].current()
                    assets.append(assembled.outputs["asset"])
                    releases.append(published.outputs["release"])
                    release = store.read_structured(releases[-1])
                    assert release["asset_definition"] == {"artifact_id": assets[-1].artifact_id}
                    repo.verify_reference_closure(releases[-1])
                    records = [
                        store.read_structured(ArtifactRef(**ref))
                        for path, ref in release["files"].items()
                        if path.startswith("provenance/assembly-")
                    ]
                    assert len(records) == 1
                    assert records[0]["node_id"] == name + "_assemble"
                    assert records[0]["output_artifact_id"] == assets[-1].artifact_id
                assert assets[0] != assets[1]
                assert releases[0] != releases[1]
            completed = run.dag.node_states
            assert all(len(state.attempts) == 1 for state in completed.values())
    with DagRepository(store, tmp_path / "core") as repo:
        restored = DagEngine(repo, registry).recover(run.run_id)
        assert restored.dag.node_states == completed


def test_remote_comparison_template_compiles_two_independent_releases():
    registry = AdapterRegistry()
    config = configuration()
    config["profiles"] = {name + "-service": value for name, value in config["profiles"].items()}
    config["default_profile"] = "first-service"
    register_remote_shape_profiles(registry, config)
    graph = yaml.safe_load(Path("examples/remote-shape-compare.yaml").read_text())
    plan = registry.bind_plan(
        compile_pipeline(
            _pipeline_from_raw(graph), load_default_operator_specs(), require_explicit_joins=True
        )
    )
    assert len(plan.bindings) == 10
    assert plan.bindings["first_shape"].parameters["service_id"] == "first"
    assert plan.bindings["second_shape"].parameters["service_id"] == "second"
    assert graph["nodes"]["first_publish"]["inputs"]["asset"] == "first_assemble.outputs.asset"
    assert graph["nodes"]["second_publish"]["inputs"]["asset"] == "second_assemble.outputs.asset"

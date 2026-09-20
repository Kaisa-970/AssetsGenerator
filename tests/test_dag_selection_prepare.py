from pathlib import Path

from PIL import Image
from test_remote_http import request
from test_remote_service_http import serve
from test_remote_shape_service import Backend
from test_workbench_engine import fixture_engine

from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.dag_profiles import image_adapter_registry
from assets_generator.dag_remote_profiles import register_remote_shape_profiles
from assets_generator.pipeline import compile_pipeline, load_default_operator_specs, load_pipeline
from assets_generator.remote_service_worker import execute_service_job
from assets_generator.remote_shape_service import ShapeServiceHandler


def test_confirmed_rgb_selection_connects_to_remote_shape_and_release(tmp_path):
    store, image, profile = fixture_engine(tmp_path)
    with serve(tmp_path / "service.sqlite") as (remote, client, _):
        identity = request().identity
        registry = image_adapter_registry({"local": profile}, "local")
        register_remote_shape_profiles(
            registry,
            {
                "default_profile": "remote",
                "profiles": {
                    "remote": {
                        "endpoint": client.endpoint,
                        "service_id": identity.service_id,
                        "backend_digest": identity.backend_digest,
                    }
                },
            },
        )
        plan = registry.bind_plan(
            compile_pipeline(
                load_pipeline(Path("pipelines/remote_selected_image_asset_v1.yaml")),
                load_default_operator_specs(),
                require_explicit_joins=True,
            )
        )
        with DagRepository(store, tmp_path / "core") as repo:
            engine = DagEngine(repo, registry)
            run = engine.drain(engine.create(plan, {"image": image}).run_id)
            assert run.status == "waiting_for_input"
            assert remote.list_jobs()["jobs"] == []
            run = engine.decide(
                run.run_id,
                "choose_object",
                expected_revision=run.dag.revision,
                idempotency_key="confirmed",
                reviewer="CPU automated fixture",
                payload={"proposal_id": "p0", "invert": False, "keep_largest": True},
            )
            assert run.status == "running"
            prepared = run.dag.node_states["prepare"].current()
            assert prepared.status == "succeeded"
            rgba = prepared.outputs["rgba"]
            with Image.open(store.blob_path(rgba)) as decoded:
                assert decoded.mode == "RGBA"
                assert decoded.getchannel("A").getextrema() == (0, 255)
            shape = run.dag.node_states["shape"].current()
            assert shape.resolved_inputs["image"] == rgba
            req = remote.request_for(shape.remote_binding.submission_key)
            assert (
                execute_service_job(
                    remote,
                    req,
                    ShapeServiceHandler(identity, tmp_path / "work", Backend, lambda: identity),
                ).state
                == "succeeded"
            )
            completed = engine.drain(run.run_id)
            assert completed.status == "succeeded"
            repo.verify_reference_closure(
                completed.dag.node_states["publish"].current().outputs["release"]
            )
    with DagRepository(store, tmp_path / "core") as repo:
        assert (
            DagEngine(repo, registry).recover(run.run_id).dag.node_states
            == completed.dag.node_states
        )

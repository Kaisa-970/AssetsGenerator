import io

from PIL import Image
from test_remote_http import request
from test_remote_service_http import serve
from test_remote_shape_service import Backend

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import PortSpec
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.dag_remote_shape import RemoteShapeAdapter
from assets_generator.pipeline import (
    PipelineDefinition,
    compile_pipeline,
    load_default_operator_specs,
)
from assets_generator.remote_service_worker import execute_service_job
from assets_generator.remote_shape_service import ShapeServiceHandler


def test_existing_shape_operator_runs_through_durable_service(tmp_path):
    identity = request().identity
    store = LocalArtifactStore(tmp_path / "core-store")
    out = io.BytesIO()
    Image.new("RGBA", (2, 2), (255, 0, 0, 255)).save(out, format="PNG")
    image = store.persist_bytes(
        out.getvalue(),
        kind="rgba_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGBA"},
    )
    with serve(tmp_path / "service.sqlite") as (service, client, _):
        registry = AdapterRegistry()
        registry.register(RemoteShapeAdapter(client.endpoint, identity))
        plan = registry.bind_plan(
            compile_pipeline(
                PipelineDefinition(
                    "remote_shape_test",
                    "1",
                    {"image": PortSpec(("rgba_image",), carriers=("artifact_ref",))},
                    {
                        "shape": {
                            "operator": "shape_generation@1",
                            "adapter": "remote_shape@1",
                            "inputs": {"image": "pipeline.inputs.image"},
                        }
                    },
                ),
                load_default_operator_specs(),
                require_explicit_joins=True,
            )
        )
        with DagRepository(store, tmp_path / "core") as repo:
            engine = DagEngine(repo, registry)
            run = engine.drain(engine.create(plan, {"image": image}).run_id)
            attempt = run.dag.node_states["shape"].current()
            assert attempt.status == "running"
            req = service.request_for(attempt.remote_binding.submission_key)
            handler = ShapeServiceHandler(identity, tmp_path / "work", Backend, lambda: identity)
            assert execute_service_job(service, req, handler).state == "succeeded"
            completed = engine.drain(run.run_id)
            assert completed.status == "succeeded"
            result = completed.dag.node_states["shape"].current()
            assert set(result.outputs) == {"mesh", "material", "native_frame"}
            assert result.outputs["native_frame"].value["frame_id"] == "native"
            assert result.remote_result is not None
            assert store.verify_digest(result.outputs["mesh"])
            assert len(completed.dag.node_states["shape"].attempts) == 1
    with DagRepository(store, tmp_path / "core") as repo:
        assert (
            DagEngine(repo, registry).recover(run.run_id).dag.node_states
            == completed.dag.node_states
        )

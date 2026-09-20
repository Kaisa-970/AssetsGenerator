import io
from dataclasses import replace
from pathlib import Path

import pytest
import trimesh
from PIL import Image
from test_remote_http import request
from test_remote_service_http import serve
from test_remote_shape_service import Backend

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_asset_assembly import (
    ShapeAssetAssemblyAdapter,
    validate_shape_asset_inputs,
)
from assets_generator.dag_asset_export import AssetExportAdapter
from assets_generator.dag_canonicalize import CanonicalizeAdapter
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_geometry_validation import GeometryValidationAdapter
from assets_generator.dag_persistence import DagRepository
from assets_generator.dag_remote_shape import RemoteShapeAdapter
from assets_generator.operators import validate_geometry
from assets_generator.pipeline import (
    compile_pipeline,
    load_default_operator_specs,
    load_pipeline,
)
from assets_generator.relations import CanonicalMeshSourceValidator, RuntimeRelationContext
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
        registry.register(CanonicalizeAdapter())
        registry.register(GeometryValidationAdapter())
        registry.register(ShapeAssetAssemblyAdapter())
        registry.register(AssetExportAdapter())
        plan = registry.bind_plan(
            compile_pipeline(
                load_pipeline(Path(__file__).parents[1] / "pipelines/remote_shape_asset_v1.yaml"),
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
            published = completed.dag.node_states["publish"].current()
            repo.verify_reference_closure(published.outputs["release"])
            release = store.read_structured(published.outputs["release"])
            assert release["asset_definition"] == {
                "artifact_id": completed.dag.node_states["assemble"]
                .current()
                .outputs["asset"]
                .artifact_id
            }
            scene = trimesh.load(
                io.BytesIO(store.blob_path(published.outputs["glb"]).read_bytes()),
                file_type="glb",
                force="scene",
            )
            assert sum(len(g.faces) for g in scene.geometry.values()) > 0
            assert all(len(state.attempts) == 1 for state in completed.dag.node_states.values())
            assembled = completed.dag.node_states["assemble"].current()
            valid_inputs = dict(assembled.resolved_inputs)
            other_image = store.persist_bytes(
                b"another-image", kind="rgba_image", schema_name="png", schema_version="1.0"
            )
            with pytest.raises(ContractError, match="lineage"):
                validate_shape_asset_inputs(
                    store, {**valid_inputs, "image": other_image}, completed.run_id
                )
            with pytest.raises(ContractError, match="lineage"):
                validate_shape_asset_inputs(store, valid_inputs, "another_run")
            wrong_spatial = replace(
                valid_inputs["spatial"],
                value={
                    **valid_inputs["spatial"].value,
                    "aabb": {"minimum": [100, 100, 100], "maximum": [101, 101, 101]},
                },
            )
            with pytest.raises(ContractError, match="bounds"):
                validate_shape_asset_inputs(
                    store, {**valid_inputs, "spatial": wrong_spatial}, completed.run_id
                )
            quality = completed.dag.node_states["quality"].current()
            report = store.read_structured(quality.outputs["report"])
            assert report["overall_status"] in {"pass", "warn"}
            assert (
                next(c for c in report["checks"] if c["check_id"] == "mandatory_provenance")[
                    "status"
                ]
                == "pass"
            )
            canonical = completed.dag.node_states["canonical"].current()

            wrong_run = validate_geometry(
                store,
                canonical.outputs["mesh"],
                derived_from=completed.dag.node_states["shape"].current().outputs["mesh"],
                run_id="another_run",
                canonical_node_id=None,
            )
            assert (
                next(c for c in wrong_run.checks if c.check_id == "mandatory_provenance").status
                == "fail"
            )

            with pytest.raises(ContractError, match="does not derive"):
                CanonicalMeshSourceValidator().validate_runtime(
                    RuntimeRelationContext(
                        "geometry_validation@1",
                        ("mesh", "source_mesh"),
                        {
                            "mesh": canonical.outputs["mesh"],
                            "source_mesh": canonical.outputs["mesh"],
                        },
                        store,
                    )
                )
            assert canonical.outputs["canonical_frame"].value["up_axis"] == "+Z"
            record = store.read_structured(canonical.provenance["mesh"][0])
            assert record["parameters"]["node_parameters"]["rule_version"] == "phase1-v1"
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

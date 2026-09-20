from pathlib import Path

import pytest
from PIL import Image
from test_remote_http import request
from test_remote_service_http import serve
from test_remote_shape_service import Backend
from test_workbench_engine import fixture_engine

from assets_generator.dag_adapters import NodeExecutionContext
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.dag_profiles import image_adapter_registry
from assets_generator.dag_remote_profiles import register_remote_shape_profiles
from assets_generator.dag_selection_prepare import SelectionPrepareAdapter
from assets_generator.models import StructuredValue
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
            binding = prepared.resolved_inputs["binding"]
            raw_binding = store.read_structured(binding)
            changed = store.persist_structured(
                StructuredValue(
                    "quality_evidence",
                    "SelectionInputBinding",
                    "1.0",
                    {
                        **raw_binding,
                        "original_mask": raw_binding["final_mask"],
                        "schema_version": "unexpected",
                    },
                )
            )
            with pytest.raises(ValueError, match="differs from confirmed"):
                SelectionPrepareAdapter().execute(
                    NodeExecutionContext(
                        run.run_id,
                        "check",
                        {"binding": changed},
                        {},
                        store,
                    )
                )
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
            from assets_generator.dag_asset_assembly import shape_observation_id

            assembled = completed.dag.node_states["assemble"].current()
            observation = prepared.outputs["observations"]
            assert assembled.resolved_inputs["observations"] == observation
            asset = store.read_structured(assembled.outputs["asset"])
            assert asset["source_observation_ids"] == [
                store.read_structured(observation)["observation_id"]
            ]
            with pytest.raises(ValueError, match="do not match prepared"):
                shape_observation_id(store, {"observations": observation, "image": image})
            assert shape_observation_id(store, {"image": rgba}) is None
            # Comparison must not regenerate a missing prepared image.
            rgba_path = store.blob_path(rgba)
            rgba_bytes = rgba_path.read_bytes()
            rgba_path.unlink()
            with pytest.raises(ValueError):
                shape_observation_id(store, {"observations": observation, "image": rgba})
            assert not rgba_path.exists()
            rgba_path.write_bytes(rgba_bytes)

            repo.verify_reference_closure(
                completed.dag.node_states["publish"].current().outputs["release"]
            )
    with DagRepository(store, tmp_path / "core") as repo:
        assert (
            DagEngine(repo, registry).recover(run.run_id).dag.node_states
            == completed.dag.node_states
        )

    # A missing historical mask must stay missing, never regenerated by validation.
    from assets_generator.models import ArtifactRef

    mask = ArtifactRef(**raw_binding["final_mask"])
    blob = store.blob_path(mask)
    blob.unlink()
    with pytest.raises(ValueError, match="missing or corrupt"):
        SelectionPrepareAdapter().execute(
            NodeExecutionContext(
                run.run_id,
                "check_missing",
                {"binding": binding},
                {},
                store,
            )
        )
    assert not blob.exists()

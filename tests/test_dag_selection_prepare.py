from copy import deepcopy
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


@pytest.mark.parametrize("proposal_only", [False, True])
@pytest.mark.parametrize("abandon_first", [False, True])
def test_confirmed_rgb_selection_connects_to_remote_shape_and_release(
    tmp_path, proposal_only, abandon_first
):
    store, image, profile = fixture_engine(tmp_path)
    with serve(tmp_path / "service.sqlite") as (remote, client, _):
        identity = request().identity
        if proposal_only:
            from assets_generator.dag_profiles import proposal_adapter_registry
            from assets_generator.workbench_profiles import ProposalProfile

            proposal = ProposalProfile(
                "local", profile.proposer, profile.proposal_identity, test_only=True
            )
            registry = proposal_adapter_registry({"local": proposal}, "local")
            assert all(item["name"] != "image_build" for item in registry.catalog())
        else:
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
            ancestors = deepcopy(
                {
                    name: run.dag.node_states[name]
                    for name in ("candidates", "choose_object", "prepare")
                }
            )
            receipts = deepcopy(run.dag.receipts)
            failed_shape = None
            if abandon_first:
                import sys

                from assets_generator.remote_service_process import ServiceProcessWorker
                from assets_generator.worker import ProcessJobRequest

                remote.transition(req, expected="queued", state="running")
                ServiceProcessWorker(remote, req).run(
                    ProcessJobRequest([sys.executable, "-c", "pass"], tmp_path, 10, "unpublished")
                )
                remote.abandon_exited_job(req)
                failed = engine.drain(run.run_id)
                assert failed.status == "failed"
                failed_shape = deepcopy(failed.dag.node_states["shape"].current())
                assert failed_shape.error_code == "SERVICE_RESULT_ABANDONED"
                # A fresh engine must preserve upstream execution and human decision evidence.
                engine = DagEngine(repo, registry)
                recovered = engine.recover(run.run_id)
                retried = engine.retry(run.run_id, "shape", recovered.dag.revision)
                assert retried.dag.receipts == receipts
                for name, state in ancestors.items():
                    assert retried.dag.node_states[name] == state
                shape = retried.dag.node_states["shape"].current()
                assert shape.remote_binding.submission_key != req.submission_key
                assert shape.resolved_inputs["image"] == rgba
                assert remote.lookup(req).state == "failed"
                req = remote.request_for(shape.remote_binding.submission_key)
                assert remote.lookup(req).state == "queued"
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
            assert completed.dag.receipts == receipts
            for name, state in ancestors.items():
                assert completed.dag.node_states[name] == state
            for name, state in completed.dag.node_states.items():
                assert len(state.attempts) == (2 if abandon_first and name == "shape" else 1)
            if failed_shape is not None:
                assert completed.dag.node_states["shape"].attempts[0] == failed_shape
                repo.verify_reference_closure(failed_shape.remote_result)
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

            release_ref = completed.dag.node_states["publish"].current().outputs["release"]
            repo.verify_reference_closure(release_ref)
            release = store.read_structured(release_ref)
            evidence_files = [
                value
                for name, value in release["files"].items()
                if name.startswith("provenance/assembly-")
            ]
            assert len(evidence_files) == 1
            from assets_generator.models import ArtifactRef

            evidence = store.read_structured(ArtifactRef(**evidence_files[0]))
            assert evidence["output_artifact_id"] == assembled.outputs["asset"].artifact_id
            assert observation.artifact_id in evidence["derived_from_artifact_ids"]
            # A release-only verifier must detect loss of the original observation.
            observation_blob = store.blob_path(observation)
            observation_bytes = observation_blob.read_bytes()
            observation_blob.unlink()
            with pytest.raises(ValueError, match="missing/corrupt dependency"):
                repo.verify_reference_closure(release_ref)
            observation_blob.write_bytes(observation_bytes)

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

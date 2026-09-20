from dataclasses import replace

import pytest
from test_multi_view_workflow import (
    ContractGeometryFrontend,
    ContractReconstruction,
    WrongGeometryFrontend,
    _observations,
    _plan,
)
from test_workbench_foundation import INPUT, advance, receipt, run

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.models import BuildRun, StructuredValue
from assets_generator.multi_view_workflow import build_multi_view_asset, prepared_backend_binding
from assets_generator.serialization import to_primitive
from assets_generator.workbench_context import ChildRunContext
from assets_generator.workbench_models import ChildRegistration
from assets_generator.workbench_persistence import WorkbenchRepository
from assets_generator.workbench_state import PrepareStage


def evidence_for(store, observations, geometry, reconstruction, plan):
    geometry_ref = store.persist_structured(
        StructuredValue(
            "quality_evidence",
            "GeometryFrontendEvidence",
            "1.0",
            {
                **to_primitive(geometry),
                "observations": to_primitive(observations),
                "backend_binding": prepared_backend_binding(plan, "estimate_geometry"),
            },
        )
    )
    reconstruction_ref = store.persist_structured(
        StructuredValue(
            "quality_evidence",
            "ReconstructionEvidence",
            "1.0",
            {
                **to_primitive(reconstruction),
                "observations": to_primitive(observations),
                "geometry_evidence": to_primitive(geometry_ref),
                "backend_binding": prepared_backend_binding(plan, "reconstruct"),
            },
        )
    )
    return {"geometry": geometry_ref, "reconstruction": reconstruction_ref}


def refresh_evidence(store, kwargs):
    kwargs["prepared_evidence"] = evidence_for(
        store,
        kwargs["observations"],
        kwargs["prepared_geometry"],
        kwargs["prepared_reconstruction"],
        kwargs["resolved_plan"],
    )


def prepared(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    observations = _observations(tmp_path, store)
    frontend = ContractGeometryFrontend()
    reconstruction = ContractReconstruction()
    geometry = frontend.estimate(store, observations)
    result = reconstruction.reconstruct(
        store, observations, geometry.cameras, geometry.depths, geometry.points
    )
    evidence = evidence_for(store, observations, geometry, result, _plan(frontend, reconstruction))
    return store, {
        "observations": observations,
        "store_path": store.root,
        "output_path": tmp_path / "release",
        "resolved_plan": _plan(frontend, reconstruction),
        "prepared_geometry": geometry,
        "prepared_reconstruction": result,
        "prepared_evidence": evidence,
    }


def test_prepared_results_skip_backends_but_preserve_identity_and_evidence(tmp_path, monkeypatch):
    store, kwargs = prepared(tmp_path)
    monkeypatch.setattr(
        ContractGeometryFrontend, "estimate", lambda *a: pytest.fail("re-inference")
    )
    monkeypatch.setattr(
        ContractReconstruction, "reconstruct", lambda *a: pytest.fail("re-inference")
    )
    result = build_multi_view_asset(**kwargs, run_id="run_prepared")
    child = WorkbenchRepository(store, tmp_path / "repository").load(result.run_id)
    assert child.status == "succeeded"
    assert [a.execution_mode for a in child.node_attempts[:2]] == ["reused_input", "reused_input"]
    assert child.resolved_backend_versions == {
        "estimate_geometry": "geometry-test",
        "reconstruct": "reconstruction-test",
    }
    for key, evidence in kwargs["prepared_evidence"].items():
        assert child.inputs[f"prepared_{key}"] == evidence
    records = [store.read_structured(ref) for ref in store.find_artifacts("provenance_record")]
    for node, key in [("estimate_geometry", "geometry"), ("reconstruct", "reconstruction")]:
        matching = [record for record in records if record["node_id"] == node]
        assert matching
        for record in matching:
            assert (
                kwargs["prepared_evidence"][key].artifact_id in record["derived_from_artifact_ids"]
            )
            assert record["parameters"]["execution_mode"] == "reused_input"
    with pytest.raises(ContractError, match="already exists"):
        build_multi_view_asset(**kwargs, run_id="run_prepared")


@pytest.mark.parametrize(
    "missing", ["prepared_geometry", "prepared_reconstruction", "prepared_evidence"]
)
def test_prepared_results_require_pair_and_evidence(tmp_path, missing):
    _, kwargs = prepared(tmp_path)
    kwargs.pop(missing)
    with pytest.raises(ContractError):
        build_multi_view_asset(**kwargs)


def test_prepared_results_still_validate_ports_and_record_failure(tmp_path):
    store, kwargs = prepared(tmp_path)
    kwargs["prepared_geometry"] = WrongGeometryFrontend().estimate(store, kwargs["observations"])
    refresh_evidence(store, kwargs)
    with pytest.raises(ContractError):
        build_multi_view_asset(**kwargs, run_id="run_invalid")
    child = WorkbenchRepository(store, tmp_path / "repository").load("run_invalid")
    assert child.status == "failed"
    assert child.node_attempts[0].status == "failed"


@pytest.mark.parametrize("fail_publish", [False, True])
def test_owned_child_checkpoints_and_terminal_publication(tmp_path, monkeypatch, fail_publish):
    store, kwargs = prepared(tmp_path)
    plan = store.persist_structured(StructuredValue("workbench_plan", "WorkbenchPlan", "1.0", {}))
    with WorkbenchRepository(store, tmp_path / "repository") as repo:
        inputs = replace(INPUT, named_actual_inputs={})
        parent = advance(
            run(plan=plan), PrepareStage(inputs, receipt(child="run_owned", digest=inputs.digest()))
        )
        repo.commit(parent)
        attempt = parent.workbench.stage_states["stage"].current()
        registration = ChildRegistration(
            "run_owned",
            "parent",
            "stage",
            1,
            attempt.input_digest,
            str(store.root / "run_owners" / "run_owned.json"),
        )
        repo.register_child(parent, registration)
        repo.commit(
            BuildRun(
                "run_owned",
                "multi_view_asset",
                "1",
                "running",
                {},
                [],
                "t",
                None,
                parent_run_id="parent",
            ),
            owner=registration,
        )
        snapshots = []
        original = repo.commit

        def commit(child, **options):
            snapshots.append([(a.node_id, a.status) for a in child.node_attempts])
            return original(child, **options)

        monkeypatch.setattr(repo, "commit", commit)
        if fail_publish:

            def fail(*args):
                raise OSError("publication unavailable")

            monkeypatch.setattr("assets_generator.multi_view_workflow._materialize_release", fail)
            with pytest.raises(OSError, match="publication unavailable"):
                build_multi_view_asset(
                    **kwargs, run_id="run_owned", child_context=ChildRunContext(repo, registration)
                )
        else:
            build_multi_view_asset(
                **kwargs, run_id="run_owned", child_context=ChildRunContext(repo, registration)
            )
        child = repo.load("run_owned")
        assert child.parent_run_id == "parent"
        assert child.status == ("failed" if fail_publish else "succeeded")
        assert child.node_attempts[-1].status == child.status
        assert any(("estimate_geometry", "running") in snapshot for snapshot in snapshots)
        assert any(("reconstruct", "running") in snapshot for snapshot in snapshots)
        with pytest.raises(ValueError, match="already started"):
            build_multi_view_asset(
                **kwargs, run_id="run_owned", child_context=ChildRunContext(repo, registration)
            )


def test_prepared_evidence_corruption_is_not_repaired(tmp_path):
    store, kwargs = prepared(tmp_path)
    evidence = kwargs["prepared_evidence"]["geometry"]
    store.blob_path(evidence).unlink()
    with pytest.raises(ContractError, match="evidence is missing or corrupt"):
        build_multi_view_asset(**kwargs, run_id="run_corrupt")
    assert not store.verify_digest(evidence)
    assert (
        WorkbenchRepository(store, tmp_path / "repository").load("run_corrupt").status == "failed"
    )


def test_prepared_reconstruction_still_validates_spatial_contract(tmp_path):
    store, kwargs = prepared(tmp_path)
    reconstructed = kwargs["prepared_reconstruction"]
    frame = reconstructed.native_frame
    kwargs["prepared_reconstruction"] = replace(
        reconstructed, native_frame=replace(frame, value={**frame.value, "unit": "meter"})
    )
    refresh_evidence(store, kwargs)
    with pytest.raises(ContractError):
        build_multi_view_asset(**kwargs, run_id="run_wrong_frame")
    child = WorkbenchRepository(store, tmp_path / "repository").load("run_wrong_frame")
    assert child.node_attempts[0].status == "succeeded"
    assert child.node_attempts[1].status == "failed"


@pytest.mark.parametrize(
    ("key", "field"),
    [
        ("geometry", name)
        for name in ("cameras", "depths", "points", "backend_metadata", "cache_hit", "observations")
    ]
    + [
        ("reconstruction", name)
        for name in (
            "mesh",
            "material",
            "native_frame",
            "components",
            "backend_metadata",
            "cache_hit",
            "observations",
            "geometry_evidence",
        )
    ],
)
def test_prepared_envelope_must_match_every_result_and_lineage_field(tmp_path, key, field):
    store, kwargs = prepared(tmp_path)
    ref = kwargs["prepared_evidence"][key]
    raw = store.read_structured(ref)
    raw[field] = {"unexpected": "different evidence"}
    schema = store.get_manifest(ref.artifact_id).identity.schema_name
    kwargs["prepared_evidence"][key] = store.persist_structured(
        StructuredValue("quality_evidence", schema, "1.0", raw)
    )
    with pytest.raises(ContractError, match=f"prepared {key} {field} differs"):
        build_multi_view_asset(**kwargs, run_id="run_mismatched_evidence")
    child = WorkbenchRepository(store, tmp_path / "repository").load("run_mismatched_evidence")
    assert child.status == "failed"
    assert not child.node_attempts
    assert not kwargs["output_path"].exists()


@pytest.mark.parametrize("key", ["geometry", "reconstruction"])
def test_prepared_envelope_rejects_unrelated_schema(tmp_path, key):
    store, kwargs = prepared(tmp_path)
    ref = kwargs["prepared_evidence"][key]
    kwargs["prepared_evidence"][key] = store.persist_structured(
        StructuredValue("workbench_plan", "UnrelatedEvidence", "1.0", store.read_structured(ref))
    )
    with pytest.raises(ContractError, match="prepared result requires"):
        build_multi_view_asset(**kwargs)


@pytest.mark.parametrize("node_id", ["estimate_geometry", "reconstruct"])
@pytest.mark.parametrize(
    "field,value", [("name", "backend_b"), ("backend_version", "other-version")]
)
def test_prepared_backend_a_cannot_be_published_as_backend_b(
    tmp_path, monkeypatch, node_id, field, value
):
    store, kwargs = prepared(tmp_path)
    original = kwargs["resolved_plan"]
    bindings = dict(original.backends)
    bindings[node_id] = replace(bindings[node_id], **{field: value})
    kwargs["resolved_plan"] = replace(original, backends=bindings)
    monkeypatch.setattr(
        ContractGeometryFrontend, "estimate", lambda *args: pytest.fail("inference")
    )
    monkeypatch.setattr(
        ContractReconstruction, "reconstruct", lambda *args: pytest.fail("inference")
    )
    with pytest.raises(ContractError, match="Backend binding differs"):
        build_multi_view_asset(**kwargs, run_id="run_wrong_backend")
    child = WorkbenchRepository(store, tmp_path / "repository").load("run_wrong_backend")
    assert child.status == "failed"
    assert child.node_attempts == []
    assert not kwargs["output_path"].exists()
    assert not store.find_artifacts("provenance_record")


@pytest.mark.parametrize("key", ["geometry", "reconstruction"])
def test_prepared_missing_backend_binding_fails_closed(tmp_path, key):
    store, kwargs = prepared(tmp_path)
    old = kwargs["prepared_evidence"][key]
    raw = store.read_structured(old)
    del raw["backend_binding"]
    manifest = store.get_manifest(old.artifact_id)
    kwargs["prepared_evidence"][key] = store.persist_structured(
        StructuredValue("quality_evidence", manifest.identity.schema_name, "1.0", raw)
    )
    with pytest.raises(ContractError, match="Backend binding differs"):
        build_multi_view_asset(**kwargs)

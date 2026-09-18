from __future__ import annotations

import json

import pytest
from test_collision import _source_release
from test_metric_scale import _measurement

from assets_generator.collision import build_collision_asset
from assets_generator.contracts import ContractError
from assets_generator.metric_scale import calibrate_metric_scale
from assets_generator.models import ArtifactRef, StructuredValue
from assets_generator.rigid_body import apply_rigid_body
from assets_generator.serialization import to_primitive


def _metric_release(tmp_path):
    store, source, _, _ = _source_release(tmp_path)
    collision = build_collision_asset(
        release=source,
        store_path=store.root,
        output_path=tmp_path / "collision-source",
    )
    metric = calibrate_metric_scale(
        release=collision.release_manifest,
        measurement_path=_measurement(tmp_path, store, collision.release_manifest),
        store_path=store.root,
        output_path=tmp_path / "metric-source",
    )
    return store, metric


def _properties(tmp_path, store, release, **overrides):
    release_raw = store.read_structured(release)
    asset_ref = ArtifactRef(**release_raw["asset_definition"])
    asset = store.read_structured(asset_ref)
    raw = {
        "schema_version": "1.0",
        "source_release_id": release.artifact_id,
        "source_asset_id": asset_ref.artifact_id,
        "collision_artifact_id": asset["geometry"]["collision_meshes"][0]["artifact_id"],
        "frame_id": asset["spatial"]["canonical_frame_id"],
        "unit": "meter",
        "body_type": "dynamic",
        "mass_kg": 12.5,
        "center_of_mass_m": [0.0, 0.0, 0.1],
        "inertia_kg_m2": [[1.0, 0.0, 0.0], [0.0, 1.2, 0.0], [0.0, 0.0, 1.4]],
        "static_friction": 0.7,
        "dynamic_friction": 0.5,
        "restitution": 0.1,
        "source": "user",
        "provided_by": "fixture-operator",
        **overrides,
    }
    path = tmp_path / "rigid-body.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_rigid_body_assignment_is_traceable_and_preserves_source_files(tmp_path):
    store, metric = _metric_release(tmp_path)
    source_release = store.read_structured(metric.release_manifest)
    source_bytes = {
        name: store.blob_path(ArtifactRef(**reference)).read_bytes()
        for name, reference in source_release["files"].items()
    }
    result = apply_rigid_body(
        release=metric.release_manifest,
        properties_path=_properties(tmp_path, store, metric.release_manifest),
        store_path=store.root,
        output_path=tmp_path / "rigid-release",
    )

    asset = store.read_structured(result.asset_definition)
    assert asset["physics"] == {
        "schema_version": "1.0",
        "body_type": "dynamic",
        "mass_kg": 12.5,
        "center_of_mass_m": [0.0, 0.0, 0.1],
        "inertia_kg_m2": [[1.0, 0.0, 0.0], [0.0, 1.2, 0.0], [0.0, 0.0, 1.4]],
        "static_friction": 0.7,
        "dynamic_friction": 0.5,
        "restitution": 0.1,
        "collision_mesh": {"artifact_id": metric.collision_mesh.artifact_id},
        "frame_id": "asset_canonical",
        "unit": "meter",
        "source": "user",
        "provided_by": "fixture-operator",
        "evidence": {"artifact_id": result.properties.artifact_id},
    }
    assert asset["geometry"] == store.read_structured(metric.asset_definition)["geometry"]
    release = store.read_structured(result.release_manifest)
    for name, data in source_bytes.items():
        assert release["files"][name] == source_release["files"][name]
        assert (result.output_directory / name).read_bytes() == data
    assert store.read_structured(result.quality_report)["overall_status"] == "pass"
    run = store.get_build_run(result.run_id)
    assert run["status"] == "succeeded"
    assert [attempt["node_id"] for attempt in run["node_attempts"]] == [
        "import_rigid_body_properties",
        "assign_rigid_body",
        "derive_rigid_body_release",
        "materialize_release",
    ]
    assert json.loads((result.output_directory / "run.json").read_text())["status"] == "succeeded"
    records = [
        store.read_structured(reference)
        for reference in store.find_artifacts("provenance_record")
        if store.read_structured(reference)["run_id"] == result.run_id
    ]
    assert {record["output_artifact_id"] for record in records} >= {
        result.properties.artifact_id,
        result.quality_report.artifact_id,
        result.asset_definition.artifact_id,
        result.release_manifest.artifact_id,
    }


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"body_type": "static"}, "only supports dynamic"),
        ({"mass_kg": True}, "mass_kg must be"),
        ({"mass_kg": 0}, "mass_kg must be positive"),
        ({"center_of_mass_m": [0.0, float("inf"), 0.0]}, "must be a finite number"),
        (
            {"inertia_kg_m2": [[1.0, 0.1, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]},
            "must be symmetric",
        ),
        (
            {"inertia_kg_m2": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 3.0]]},
            "triangle inequality",
        ),
        (
            {
                "inertia_kg_m2": [
                    [1e-15, 0.0, 0.0],
                    [0.0, 1e-15, 0.0],
                    [0.0, 0.0, 3e-15],
                ]
            },
            "triangle inequality",
        ),
        ({"dynamic_friction": 0.8}, "cannot exceed"),
        ({"static_friction": -0.1}, "must be nonnegative"),
        ({"restitution": 1.1}, "between 0 and 1"),
    ],
)
def test_rigid_body_rejects_invalid_explicit_properties(tmp_path, override, message):
    store, metric = _metric_release(tmp_path)
    before = set((store.root / "runs").glob("*.json"))
    with pytest.raises(ContractError, match=message):
        apply_rigid_body(
            release=metric.release_manifest,
            properties_path=_properties(tmp_path, store, metric.release_manifest, **override),
            store_path=store.root,
            output_path=tmp_path / "bad",
        )
    created = set((store.root / "runs").glob("*.json")) - before
    assert len(created) == 1
    run = store.get_build_run(created.pop().stem)
    assert run["status"] == "failed"
    assert run["node_attempts"][-1]["node_id"] == "import_rigid_body_properties"
    assert run["node_attempts"][-1]["error_code"] == "contract_error"


def test_rigid_body_records_unreadable_json_as_failed_import(tmp_path):
    store, metric = _metric_release(tmp_path)
    path = tmp_path / "invalid.json"
    path.write_text("{", encoding="utf-8")
    before = set((store.root / "runs").glob("*.json"))
    with pytest.raises(ContractError, match="readable UTF-8 JSON"):
        apply_rigid_body(
            release=metric.release_manifest,
            properties_path=path,
            store_path=store.root,
            output_path=tmp_path / "bad",
        )
    created = set((store.root / "runs").glob("*.json")) - before
    run = store.get_build_run(created.pop().stem)
    assert run["node_attempts"][-1]["node_id"] == "import_rigid_body_properties"
    assert run["node_attempts"][-1]["error_code"] == "contract_error"


def test_rigid_body_accepts_friction_above_one(tmp_path):
    store, metric = _metric_release(tmp_path)
    result = apply_rigid_body(
        release=metric.release_manifest,
        properties_path=_properties(
            tmp_path,
            store,
            metric.release_manifest,
            static_friction=1.4,
            dynamic_friction=1.2,
        ),
        store_path=store.root,
        output_path=tmp_path / "high-friction",
    )
    physics = store.read_structured(result.asset_definition)["physics"]
    assert physics["static_friction"] == 1.4
    assert physics["dynamic_friction"] == 1.2


def test_rigid_body_normalizes_negative_zero(tmp_path):
    store, metric = _metric_release(tmp_path)
    result = apply_rigid_body(
        release=metric.release_manifest,
        properties_path=_properties(
            tmp_path,
            store,
            metric.release_manifest,
            center_of_mass_m=[-0.0, 0.0, -0.0],
        ),
        store_path=store.root,
        output_path=tmp_path / "normalized-zero",
    )
    assert store.read_structured(result.properties)["center_of_mass_m"] == [0.0, 0.0, 0.0]


@pytest.mark.parametrize(
    "override",
    [
        {"source_release_id": "sha256:" + "1" * 64},
        {"source_asset_id": "sha256:" + "2" * 64},
        {"collision_artifact_id": "sha256:" + "3" * 64},
        {"frame_id": "other_frame"},
    ],
)
def test_rigid_body_rejects_properties_for_another_asset(tmp_path, override):
    store, metric = _metric_release(tmp_path)
    with pytest.raises(ContractError, match="do not match"):
        apply_rigid_body(
            release=metric.release_manifest,
            properties_path=_properties(tmp_path, store, metric.release_manifest, **override),
            store_path=store.root,
            output_path=tmp_path / "bad-binding",
        )


def test_rigid_body_requires_metric_collision_and_null_physics(tmp_path):
    store, metric = _metric_release(tmp_path)
    raw = store.read_structured(metric.release_manifest)
    asset = store.read_structured(ArtifactRef(**raw["asset_definition"]))
    asset["geometry"]["collision_meshes"] = []
    asset_ref = store.persist_structured(
        StructuredValue("asset_definition", "AssetDefinition", "1.0", asset)
    )
    raw["asset_definition"] = to_primitive(asset_ref)
    release = store.persist_structured(StructuredValue("asset_release", "AssetRelease", "1.0", raw))
    path = _properties(tmp_path, store, metric.release_manifest)
    with pytest.raises(ContractError, match="exactly one collision"):
        apply_rigid_body(
            release=release,
            properties_path=path,
            store_path=store.root,
            output_path=tmp_path / "bad-collision",
        )


def test_rigid_body_requires_release_collision_glb(tmp_path):
    store, metric = _metric_release(tmp_path)
    raw = store.read_structured(metric.release_manifest)
    del raw["files"]["geometry/collision.glb"]
    release = store.persist_structured(StructuredValue("asset_release", "AssetRelease", "1.0", raw))
    with pytest.raises(ContractError, match="must contain geometry/collision.glb"):
        apply_rigid_body(
            release=release,
            properties_path=_properties(tmp_path, store, release),
            store_path=store.root,
            output_path=tmp_path / "missing-collision-glb",
        )


def test_rigid_body_rejects_collision_glb_without_matching_provenance(tmp_path):
    store, metric = _metric_release(tmp_path)
    raw = store.read_structured(metric.release_manifest)
    raw["files"]["geometry/collision.glb"] = raw["files"]["geometry/visual.glb"]
    release = store.persist_structured(StructuredValue("asset_release", "AssetRelease", "1.0", raw))
    with pytest.raises(ContractError, match="lacks provenance"):
        apply_rigid_body(
            release=release,
            properties_path=_properties(tmp_path, store, release),
            store_path=store.root,
            output_path=tmp_path / "forged-collision-glb",
        )


def test_rigid_body_rejects_existing_physics(tmp_path):
    store, metric = _metric_release(tmp_path)
    raw = store.read_structured(metric.release_manifest)
    asset = store.read_structured(ArtifactRef(**raw["asset_definition"]))
    asset["physics"] = {"schema_version": "legacy"}
    asset_ref = store.persist_structured(
        StructuredValue("asset_definition", "AssetDefinition", "1.0", asset)
    )
    raw["asset_definition"] = to_primitive(asset_ref)
    release = store.persist_structured(StructuredValue("asset_release", "AssetRelease", "1.0", raw))
    with pytest.raises(ContractError, match="physics to be null"):
        apply_rigid_body(
            release=release,
            properties_path=_properties(tmp_path, store, release),
            store_path=store.root,
            output_path=tmp_path / "existing-physics",
        )


def test_rigid_body_materialization_failure_is_atomic(tmp_path, monkeypatch):
    store, metric = _metric_release(tmp_path)
    monkeypatch.setattr(
        "assets_generator.rigid_body._materialize_release",
        lambda *args: (_ for _ in ()).throw(OSError("publish failed")),
    )
    before = set((store.root / "runs").glob("*.json"))
    with pytest.raises(OSError, match="publish failed"):
        apply_rigid_body(
            release=metric.release_manifest,
            properties_path=_properties(tmp_path, store, metric.release_manifest),
            store_path=store.root,
            output_path=tmp_path / "failed",
        )
    created = set((store.root / "runs").glob("*.json")) - before
    run = store.get_build_run(created.pop().stem)
    assert run["status"] == "failed"
    assert run["node_attempts"][-1]["error_code"] == "release_failed"
    assert not (tmp_path / "failed").exists()


def test_rigid_body_final_publish_failure_is_atomic(tmp_path, monkeypatch):
    store, metric = _metric_release(tmp_path)
    monkeypatch.setattr(
        "assets_generator.rigid_body._publish_staged_release",
        lambda *args: (_ for _ in ()).throw(OSError("final publish failed")),
    )
    before = set((store.root / "runs").glob("*.json"))
    with pytest.raises(OSError, match="final publish failed"):
        apply_rigid_body(
            release=metric.release_manifest,
            properties_path=_properties(tmp_path, store, metric.release_manifest),
            store_path=store.root,
            output_path=tmp_path / "failed-final",
        )
    created = set((store.root / "runs").glob("*.json")) - before
    run = store.get_build_run(created.pop().stem)
    assert run["status"] == "failed"
    assert run["node_attempts"][-1]["node_id"] == "materialize_release"
    assert run["node_attempts"][-1]["error_code"] == "release_failed"
    assert not (tmp_path / "failed-final").exists()
    assert not list(tmp_path.glob(".failed-final.*.staged"))


def test_rigid_body_does_not_replace_concurrent_destination(tmp_path, monkeypatch):
    store, metric = _metric_release(tmp_path)
    output = tmp_path / "concurrent-output"
    original = __import__("assets_generator.rigid_body", fromlist=["_publish_staged_release"])
    publish = original._publish_staged_release

    def create_destination_then_publish(staging, destination):
        destination.mkdir()
        (destination / "owner.txt").write_text("other publisher", encoding="utf-8")
        publish(staging, destination)

    monkeypatch.setattr(
        "assets_generator.rigid_body._publish_staged_release",
        create_destination_then_publish,
    )
    with pytest.raises(FileExistsError):
        apply_rigid_body(
            release=metric.release_manifest,
            properties_path=_properties(tmp_path, store, metric.release_manifest),
            store_path=store.root,
            output_path=output,
        )
    assert (output / "owner.txt").read_text(encoding="utf-8") == "other publisher"
    assert not (output / "asset.json").exists()
    assert not list(tmp_path.glob(".concurrent-output.*.staged"))


def test_rigid_body_cli(tmp_path, monkeypatch, capsys):
    from assets_generator.cli import main

    store, metric = _metric_release(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "assets-generator",
            "apply-rigid-body",
            "--release",
            metric.release_manifest.artifact_id,
            "--properties",
            str(_properties(tmp_path, store, metric.release_manifest)),
            "--store",
            str(store.root),
            "--output",
            str(tmp_path / "cli-release"),
        ],
    )
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert store.verify_digest(ArtifactRef(**result["asset_definition"]))
    assert (tmp_path / "cli-release/evidence/rigid-body-properties.json").is_file()

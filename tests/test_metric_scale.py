from __future__ import annotations

import json

import numpy as np
import pytest
from test_collision import _source_release

from assets_generator.collision import build_collision_asset
from assets_generator.contracts import ContractError
from assets_generator.metric_scale import calibrate_metric_scale
from assets_generator.models import ArtifactRef, StructuredValue
from assets_generator.operators import _load_scene, _scene_vertices
from assets_generator.serialization import to_primitive
from assets_generator.workflow import _persist_provenance


def _measurement(tmp_path, store=None, release=None, **overrides):
    source_asset_id = "sha256:" + "0" * 64
    visual_artifact_id = "sha256:" + "0" * 64
    frame_id = "asset_canonical"
    if store is not None and release is not None:
        release_raw = store.read_structured(release)
        source_asset = ArtifactRef(**release_raw["asset_definition"])
        asset = store.read_structured(source_asset)
        source_asset_id = source_asset.artifact_id
        visual_artifact_id = asset["geometry"]["visual_meshes"][0]["artifact_id"]
        frame_id = asset["spatial"]["canonical_frame_id"]
    raw = {
        "schema_version": "1.0",
        "source_asset_id": source_asset_id,
        "visual_artifact_id": visual_artifact_id,
        "frame_id": frame_id,
        "unit": "relative_unit",
        "point_a": [-1.0, 0.0, 0.0],
        "point_b": [1.0, 0.0, 0.0],
        "distance_meters": 4.0,
        "source": "user-tape-measure",
        **overrides,
    }
    path = tmp_path / "measurement.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_metric_scale_updates_visual_collision_and_release(tmp_path):
    store, source_release, _, _ = _source_release(tmp_path)
    collision = build_collision_asset(
        release=source_release,
        store_path=store.root,
        output_path=tmp_path / "collision-source",
    )
    source_raw = store.read_structured(collision.release_manifest)
    source_asset = store.read_structured(collision.asset_definition)

    result = calibrate_metric_scale(
        release=collision.release_manifest,
        measurement_path=_measurement(tmp_path, store, collision.release_manifest),
        store_path=store.root,
        output_path=tmp_path / "metric-release",
    )

    asset = store.read_structured(result.asset_definition)
    assert asset["spatial"]["unit"] == "meter"
    assert asset["spatial"]["scale_status"] == "metric"
    assert asset["semantics"] == source_asset["semantics"]
    assert asset["physics"] is None
    visual_meta = store.get_manifest(result.visual_mesh.artifact_id).identity.identity_metadata
    assert visual_meta["unit"] == "meter"
    assert visual_meta["metric_scale_factor"] == 2.0
    assert result.collision_mesh is not None
    collision_meta = store.get_manifest(
        result.collision_mesh.artifact_id
    ).identity.identity_metadata
    assert collision_meta["unit"] == "meter"
    assert collision_meta["metric_scale_factor"] == 2.0
    vertices = _scene_vertices(_load_scene(store.blob_path(result.visual_mesh).read_bytes()))
    assert np.allclose(vertices.max(axis=0) - vertices.min(axis=0), [4.0, 2.0, 6.0])
    release = store.read_structured(result.release_manifest)
    visual_glb = ArtifactRef(**release["files"]["geometry/visual.glb"])
    assert store.get_manifest(visual_glb.artifact_id).identity.identity_metadata["unit"] == "meter"
    assert (result.output_directory / "geometry/collision.glb").is_file()
    for name, reference in source_raw["files"].items():
        if name not in {"geometry/visual.glb", "geometry/collision.glb"} and name not in {
            "qa/metric-scale-report.json",
            "evidence/metric-scale-measurement.json",
            "provenance/metric-scale-visual.json",
            "provenance/metric-scale-collision.json",
            "provenance/metric-scale-quality.json",
            "provenance/metric-scale-asset.json",
        }:
            assert release["files"][name] == reference
    assert store.read_structured(collision.asset_definition) == source_asset
    report = store.read_structured(result.quality_report)
    assert report["overall_status"] == "pass"
    assert store.get_build_run(result.run_id)["status"] == "succeeded"


def test_metric_scale_without_collision(tmp_path):
    store, release, _, _ = _source_release(tmp_path)
    result = calibrate_metric_scale(
        release=release,
        measurement_path=_measurement(tmp_path, store, release),
        store_path=store.root,
        output_path=tmp_path / "metric-release",
    )
    assert result.collision_mesh is None
    asset = store.read_structured(result.asset_definition)
    assert asset["geometry"]["collision_meshes"] == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"point_b": [-1.0, 0.0, 0.0]},
        {"distance_meters": 0.0},
        {"source": ""},
        {"point_a": [0.0, 0.0]},
    ],
)
def test_metric_scale_rejects_invalid_measurement(tmp_path, overrides):
    store, release, _, _ = _source_release(tmp_path)
    with pytest.raises(ContractError):
        calibrate_metric_scale(
            release=release,
            measurement_path=_measurement(tmp_path, store, release, **overrides),
            store_path=store.root,
            output_path=tmp_path / "bad",
        )
    assert not (tmp_path / "bad").exists()


def test_metric_scale_rejects_already_metric_asset(tmp_path):
    store, release, _, _ = _source_release(tmp_path)
    release_raw = store.read_structured(release)
    asset_ref = ArtifactRef(**release_raw["asset_definition"])
    asset = store.read_structured(asset_ref)
    asset["spatial"]["unit"] = "meter"
    asset["spatial"]["scale_status"] = "metric"
    bad_asset = store.persist_structured(
        StructuredValue("asset_definition", "AssetDefinition", "1.0", asset)
    )
    release_raw["asset_definition"] = to_primitive(bad_asset)
    bad_release = store.persist_structured(
        StructuredValue("asset_release", "AssetRelease", "1.0", release_raw)
    )
    with pytest.raises(ContractError, match="relative-scale"):
        calibrate_metric_scale(
            release=bad_release,
            measurement_path=_measurement(tmp_path, store, bad_release),
            store_path=store.root,
            output_path=tmp_path / "bad",
        )


def test_metric_scale_cli(tmp_path, monkeypatch, capsys):
    from assets_generator.cli import main

    store, release, _, _ = _source_release(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "assets-generator",
            "calibrate-scale",
            "--release",
            release.artifact_id,
            "--measurement",
            str(_measurement(tmp_path, store, release)),
            "--store",
            str(store.root),
            "--output",
            str(tmp_path / "cli-release"),
        ],
    )
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert store.verify_digest(ArtifactRef(**result["asset_definition"]))
    assert (tmp_path / "cli-release/geometry/visual.glb").is_file()


@pytest.mark.parametrize("field", ["point_clouds", "gaussians"])
def test_metric_scale_rejects_unsupported_spatial_geometry(tmp_path, field):
    store, release, _, mesh = _source_release(tmp_path)
    release_raw = store.read_structured(release)
    asset = store.read_structured(ArtifactRef(**release_raw["asset_definition"]))
    asset["geometry"][field] = [to_primitive(mesh)]
    bad_asset = store.persist_structured(
        StructuredValue("asset_definition", "AssetDefinition", "1.0", asset)
    )
    release_raw["asset_definition"] = to_primitive(bad_asset)
    bad = store.persist_structured(
        StructuredValue("asset_release", "AssetRelease", "1.0", release_raw)
    )
    with pytest.raises(ContractError, match="point clouds and gaussians"):
        calibrate_metric_scale(
            release=bad,
            measurement_path=_measurement(tmp_path, store, bad),
            store_path=store.root,
            output_path=tmp_path / "bad",
        )


def test_metric_scale_remaps_component_provenance_to_new_visual(tmp_path):
    store, release, _, mesh = _source_release(tmp_path)
    source_provenance = _persist_provenance(
        store,
        run_id="run_source_component",
        node_id="source",
        port_name="mesh",
        artifact=mesh,
        derived_from=[],
        operator="source",
        backend="test",
        backend_version="1",
        parameters={},
        seed=None,
        source="generated",
    )
    source_record = store.read_structured(source_provenance)
    release_raw = store.read_structured(release)
    asset = store.read_structured(ArtifactRef(**release_raw["asset_definition"]))
    asset["component_provenance"] = [
        {
            "component_id": "body",
            "artifact": to_primitive(mesh),
            "source": "generated",
            "provenance_ids": [source_record["provenance_id"]],
            "region_map": None,
        }
    ]
    source_asset = store.persist_structured(
        StructuredValue("asset_definition", "AssetDefinition", "1.0", asset)
    )
    release_raw["asset_definition"] = to_primitive(source_asset)
    source_release = store.persist_structured(
        StructuredValue("asset_release", "AssetRelease", "1.0", release_raw)
    )
    result = calibrate_metric_scale(
        release=source_release,
        measurement_path=_measurement(tmp_path, store, source_release),
        store_path=store.root,
        output_path=tmp_path / "metric",
    )
    component = store.read_structured(result.asset_definition)["component_provenance"][0]
    assert component["artifact"] == to_primitive(result.visual_mesh)
    records = [store.read_structured(ref) for ref in store.find_artifacts("provenance_record")]
    matched = [
        record for record in records if record["provenance_id"] in component["provenance_ids"]
    ]
    assert len(matched) == 2
    assert {record["output_artifact_id"] for record in matched} == {
        mesh.artifact_id,
        result.visual_mesh.artifact_id,
    }


def test_metric_scale_rejects_existing_physics(tmp_path):
    store, release, _, _ = _source_release(tmp_path)
    release_raw = store.read_structured(release)
    asset = store.read_structured(ArtifactRef(**release_raw["asset_definition"]))
    asset["physics"] = {"collision_margin": 0.01}
    bad_asset = store.persist_structured(
        StructuredValue("asset_definition", "AssetDefinition", "1.0", asset)
    )
    release_raw["asset_definition"] = to_primitive(bad_asset)
    bad = store.persist_structured(
        StructuredValue("asset_release", "AssetRelease", "1.0", release_raw)
    )
    with pytest.raises(ContractError, match="physics to be null"):
        calibrate_metric_scale(
            release=bad,
            measurement_path=_measurement(tmp_path, store, bad),
            store_path=store.root,
            output_path=tmp_path / "bad",
        )


@pytest.mark.parametrize(
    "override",
    [
        {"source_asset_id": "sha256:" + "1" * 64},
        {"visual_artifact_id": "sha256:" + "2" * 64},
        {"frame_id": "other_frame"},
        {"unit": "meter"},
    ],
)
def test_metric_scale_rejects_measurement_for_other_spatial_identity(tmp_path, override):
    store, release, _, _ = _source_release(tmp_path)
    with pytest.raises(ContractError):
        calibrate_metric_scale(
            release=release,
            measurement_path=_measurement(tmp_path, store, release, **override),
            store_path=store.root,
            output_path=tmp_path / "bad",
        )


def test_metric_scale_export_and_release_provenance_match_run(tmp_path):
    store, release, _, _ = _source_release(tmp_path)
    result = calibrate_metric_scale(
        release=release,
        measurement_path=_measurement(tmp_path, store, release),
        store_path=store.root,
        output_path=tmp_path / "metric",
    )
    run = store.get_build_run(result.run_id)
    records = [
        store.read_structured(ref)
        for ref in store.find_artifacts("provenance_record")
        if store.read_structured(ref)["run_id"] == result.run_id
    ]
    by_output = {record["output_artifact_id"]: record for record in records}
    release_raw = store.read_structured(result.release_manifest)
    visual_glb = ArtifactRef(**release_raw["files"]["geometry/visual.glb"])
    for node_id, port, artifact in [
        ("export_metric_scale", "visual_glb", visual_glb),
        ("derive_metric_scale_release", "release", result.release_manifest),
    ]:
        attempt = next(item for item in run["node_attempts"] if item["node_id"] == node_id)
        assert attempt["outputs"][port] == to_primitive(artifact)
        assert by_output[artifact.artifact_id]["node_id"] == node_id


def test_metric_scale_rejects_reserved_output_conflict(tmp_path):
    store, release, _, _ = _source_release(tmp_path)
    raw = store.read_structured(release)
    raw["files"]["qa/metric-scale-report.json"] = raw["files"]["qa/quality-report.json"]
    bad = store.persist_structured(StructuredValue("asset_release", "AssetRelease", "1.0", raw))
    with pytest.raises(ContractError, match="conflicts with metric output paths"):
        calibrate_metric_scale(
            release=bad,
            measurement_path=_measurement(tmp_path, store, bad),
            store_path=store.root,
            output_path=tmp_path / "bad",
        )


def test_metric_scale_materialization_failure_is_atomic(tmp_path, monkeypatch):
    store, release, _, _ = _source_release(tmp_path)
    monkeypatch.setattr(
        "assets_generator.metric_scale._materialize_release",
        lambda *args: (_ for _ in ()).throw(OSError("publish failed")),
    )
    before = set((store.root / "runs").glob("*.json"))
    with pytest.raises(OSError, match="publish failed"):
        calibrate_metric_scale(
            release=release,
            measurement_path=_measurement(tmp_path, store, release),
            store_path=store.root,
            output_path=tmp_path / "failed",
        )
    created = set((store.root / "runs").glob("*.json")) - before
    assert len(created) == 1
    run = store.get_build_run(created.pop().stem)
    assert run["status"] == "failed"
    assert run["node_attempts"][-1]["node_id"] == "materialize_release"
    assert run["node_attempts"][-1]["error_code"] == "release_failed"
    assert not (tmp_path / "failed").exists()

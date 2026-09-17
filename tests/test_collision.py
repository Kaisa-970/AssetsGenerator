from __future__ import annotations

import json

import pytest
import trimesh

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.collision import build_collision_asset
from assets_generator.contracts import ContractError
from assets_generator.models import ArtifactRef, StructuredValue
from assets_generator.serialization import to_primitive


def _source_release(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    scene = trimesh.Scene(trimesh.creation.box(extents=[2.0, 1.0, 3.0]))
    glb = scene.export(file_type="glb")
    assert isinstance(glb, bytes)
    metadata = {
        "media_type": "model/gltf-binary",
        "frame_id": "asset_canonical",
        "unit": "relative_unit",
        "up_axis": "+Z",
        "forward_axis": "+X",
    }
    mesh = store.persist_bytes(
        glb,
        kind="triangle_mesh",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata=metadata,
    )
    exported = store.persist_bytes(
        glb,
        kind="gltf_asset",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={
            **metadata,
            "frame_id": "gltf_export",
            "up_axis": "+Y",
            "forward_axis": "+Z",
        },
    )
    old_qa = store.persist_structured(
        StructuredValue(
            "quality_report",
            "QualityReport",
            "1.0",
            {"profile": "fixture", "checks": [], "overall_status": "pass"},
        )
    )
    asset = store.persist_structured(
        StructuredValue(
            "asset_definition",
            "AssetDefinition",
            "1.0",
            {
                "asset_id": "asset_source",
                "asset_version": "7.2",
                "name": "source",
                "geometry": {
                    "visual_meshes": [to_primitive(mesh)],
                    "gaussians": [],
                    "point_clouds": [],
                    "collision_meshes": [],
                },
                "appearance": {"materials": []},
                "spatial": {
                    "canonical_frame_id": "asset_canonical",
                    "bounds": {"minimum": [-1.0, -0.5, -1.5], "maximum": [1.0, 0.5, 1.5]},
                    "ground_plane": None,
                    "scale_status": "relative",
                    "unit": "relative_unit",
                    "forward_status": "declared",
                },
                "semantics": {"semantic_class": None, "source": "unknown"},
                "physics": None,
                "source_observation_ids": ["observation-fixture"],
                "quality_report_ids": [old_qa.artifact_id],
                "component_provenance": [],
            },
        )
    )
    release = store.persist_structured(
        StructuredValue(
            "asset_release",
            "AssetRelease",
            "1.0",
            {
                "asset_definition": to_primitive(asset),
                "files": {
                    "geometry/visual.glb": to_primitive(exported),
                    "qa/quality-report.json": to_primitive(old_qa),
                },
                "export_profile": "gltf2-v1",
            },
        )
    )
    return store, release, asset, mesh


def test_collision_build_is_traceable_and_preserves_source_release(tmp_path):
    store, source_release, source_asset, source_mesh = _source_release(tmp_path)
    source_raw = store.read_structured(source_release)
    source_bytes = {
        name: store.blob_path(ArtifactRef(**reference)).read_bytes()
        for name, reference in source_raw["files"].items()
    }

    result = build_collision_asset(
        release=source_release,
        store_path=store.root,
        output_path=tmp_path / "collision-release",
    )

    collision_manifest = store.get_manifest(result.collision.artifact_id)
    source_metadata = store.get_manifest(source_mesh.artifact_id).identity.identity_metadata
    assert collision_manifest.identity.kind == "collision_mesh"
    for key in ("frame_id", "unit", "up_axis", "forward_axis"):
        assert collision_manifest.identity.identity_metadata[key] == source_metadata[key]
    asset = store.read_structured(result.asset_definition)
    assert asset["asset_id"] != "asset_source"
    assert asset["asset_version"] == "1.0"
    assert asset["geometry"]["visual_meshes"] == [{"artifact_id": source_mesh.artifact_id}]
    assert asset["geometry"]["collision_meshes"] == [{"artifact_id": result.collision.artifact_id}]
    assert asset["quality_report_ids"][-1] == result.quality_report.artifact_id
    report = store.read_structured(result.quality_report)
    assert report["overall_status"] == "pass"
    assert {check["check_id"] for check in report["checks"]} >= {
        "collision_loadable",
        "collision_spatial_contract",
        "mandatory_collision_provenance",
    }
    release = store.read_structured(result.release_manifest)
    for name, reference in source_raw["files"].items():
        assert release["files"][name] == reference
        assert (result.output_directory / name).read_bytes() == source_bytes[name]
    assert (result.output_directory / "geometry/collision.glb").is_file()
    assert store.read_structured(source_asset)["geometry"]["collision_meshes"] == []
    assert store.read_structured(source_release) == source_raw
    run = store.get_build_run(result.run_id)
    assert run["status"] == "succeeded"
    assert run["inputs"]["release"] == {"artifact_id": source_release.artifact_id}


def test_collision_mesh_identity_is_deterministic(tmp_path):
    store, release, _, _ = _source_release(tmp_path)
    first = build_collision_asset(
        release=release, store_path=store.root, output_path=tmp_path / "first"
    )
    second = build_collision_asset(
        release=release, store_path=store.root, output_path=tmp_path / "second"
    )
    assert first.collision == second.collision
    assert first.collision_glb == second.collision_glb
    first_asset = store.read_structured(first.asset_definition)
    second_asset = store.read_structured(second.asset_definition)
    assert (first_asset["asset_id"], first_asset["asset_version"]) != (
        second_asset["asset_id"],
        second_asset["asset_version"],
    )


def test_collision_provenance_matches_build_run_outputs(tmp_path):
    store, release, _, _ = _source_release(tmp_path)
    result = build_collision_asset(
        release=release, store_path=store.root, output_path=tmp_path / "collision"
    )
    run = store.get_build_run(result.run_id)
    expected = {
        ("generate_collision", "collision_generation", "collision", result.collision.artifact_id),
        (
            "validate_collision",
            "collision_validation",
            "report",
            result.quality_report.artifact_id,
        ),
        (
            "derive_collision_asset",
            "collision_asset_derivation",
            "asset",
            result.asset_definition.artifact_id,
        ),
        (
            "export_collision_release",
            "collision_export",
            "glb",
            result.collision_glb.artifact_id,
        ),
        (
            "derive_collision_release",
            "collision_release",
            "release",
            result.release_manifest.artifact_id,
        ),
    }
    records = set()
    for reference in store.find_artifacts("provenance_record"):
        raw = store.read_structured(reference)
        if raw["run_id"] != result.run_id:
            continue
        node = next(item for item in run["node_attempts"] if item["node_id"] == raw["node_id"])
        port = next(
            name
            for name, value in node["outputs"].items()
            if value["artifact_id"] == raw["output_artifact_id"]
        )
        records.add((raw["node_id"], raw["operator"], port, raw["output_artifact_id"]))
    assert expected <= records


@pytest.mark.parametrize(
    ("metadata_key", "metadata_value", "spatial_key", "spatial_value"),
    [
        ("frame_id", "other_frame", "canonical_frame_id", "asset_canonical"),
        ("unit", "meter", "unit", "relative_unit"),
        ("up_axis", "+Y", "canonical_frame_id", "asset_canonical"),
        ("forward_axis", "-X", "canonical_frame_id", "asset_canonical"),
    ],
)
def test_collision_rejects_spatial_mismatch(
    tmp_path, metadata_key, metadata_value, spatial_key, spatial_value
):
    store, release, _, mesh = _source_release(tmp_path)
    source_manifest = store.get_manifest(mesh.artifact_id)
    bad_metadata = dict(source_manifest.identity.identity_metadata)
    bad_metadata[metadata_key] = metadata_value
    bad_mesh = store.persist_bytes(
        store.blob_path(mesh).read_bytes(),
        kind="triangle_mesh",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata=bad_metadata,
    )
    release_raw = store.read_structured(release)
    asset_ref = ArtifactRef(**release_raw["asset_definition"])
    asset = store.read_structured(asset_ref)
    asset["spatial"][spatial_key] = spatial_value
    asset["geometry"]["visual_meshes"] = [to_primitive(bad_mesh)]
    bad_asset = store.persist_structured(
        StructuredValue("asset_definition", "AssetDefinition", "1.0", asset)
    )
    release_raw["asset_definition"] = to_primitive(bad_asset)
    bad_release = store.persist_structured(
        StructuredValue("asset_release", "AssetRelease", "1.0", release_raw)
    )
    with pytest.raises(ContractError, match="must match AssetDefinition spatial"):
        build_collision_asset(
            release=bad_release, store_path=store.root, output_path=tmp_path / "bad-spatial"
        )


def test_collision_rejects_existing_collision_and_records_failed_run(tmp_path):
    store, release, _, _ = _source_release(tmp_path)
    raw_release = store.read_structured(release)
    asset_ref = ArtifactRef(**raw_release["asset_definition"])
    asset = store.read_structured(asset_ref)
    asset["geometry"]["collision_meshes"] = asset["geometry"]["visual_meshes"]
    bad_asset = store.persist_structured(
        StructuredValue("asset_definition", "AssetDefinition", "1.0", asset)
    )
    raw_release["asset_definition"] = to_primitive(bad_asset)
    bad_release = store.persist_structured(
        StructuredValue("asset_release", "AssetRelease", "1.0", raw_release)
    )
    before = set((store.root / "runs").glob("*.json"))
    with pytest.raises(ContractError, match="must not already contain"):
        build_collision_asset(
            release=bad_release, store_path=store.root, output_path=tmp_path / "bad"
        )
    created = set((store.root / "runs").glob("*.json")) - before
    assert len(created) == 1
    run = store.get_build_run(created.pop().stem)
    assert run["status"] == "failed"
    assert run["node_attempts"][-1]["error_code"] == "contract_error"
    assert not (tmp_path / "bad").exists()


def test_collision_rejects_release_path_conflict(tmp_path):
    store, release, _, _ = _source_release(tmp_path)
    raw = store.read_structured(release)
    raw["files"]["geometry/collision.glb"] = raw["files"]["geometry/visual.glb"]
    bad = store.persist_structured(StructuredValue("asset_release", "AssetRelease", "1.0", raw))
    with pytest.raises(ContractError, match="collision output paths"):
        build_collision_asset(release=bad, store_path=store.root, output_path=tmp_path / "bad")


def test_collision_cli(tmp_path, monkeypatch, capsys):
    from assets_generator.cli import main

    store, release, _, _ = _source_release(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "assets-generator",
            "build-collision",
            "--release",
            release.artifact_id,
            "--store",
            str(store.root),
            "--output",
            str(tmp_path / "cli-release"),
        ],
    )
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert store.verify_digest(ArtifactRef(**result["collision"]))
    assert (tmp_path / "cli-release/geometry/collision.glb").is_file()


def test_collision_materialization_failure_is_atomic(tmp_path, monkeypatch):
    store, release, _, _ = _source_release(tmp_path)
    output = tmp_path / "failed-release"
    monkeypatch.setattr(
        "assets_generator.collision._materialize_release",
        lambda *args: (_ for _ in ()).throw(OSError("publish failed")),
    )
    before = set((store.root / "runs").glob("*.json"))
    with pytest.raises(OSError, match="publish failed"):
        build_collision_asset(release=release, store_path=store.root, output_path=output)
    created = set((store.root / "runs").glob("*.json")) - before
    assert len(created) == 1
    run = store.get_build_run(created.pop().stem)
    assert run["status"] == "failed"
    assert run["node_attempts"][-1]["node_id"] == "materialize_release"
    assert run["node_attempts"][-1]["error_code"] == "release_failed"
    assert not output.exists()

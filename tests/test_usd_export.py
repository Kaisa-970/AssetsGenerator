from __future__ import annotations

import json

import pytest
from test_rigid_body import _metric_release, _properties

from assets_generator.contracts import ContractError
from assets_generator.models import StructuredValue
from assets_generator.rigid_body import apply_rigid_body
from assets_generator.serialization import to_primitive
from assets_generator.usd_export import export_usd


def _source(tmp_path):
    store, metric = _metric_release(tmp_path)
    result = apply_rigid_body(
        release=metric.release_manifest,
        properties_path=_properties(tmp_path, store, metric.release_manifest),
        store_path=store.root,
        output_path=tmp_path / "rigid",
    )
    return store, result


@pytest.fixture
def writer(monkeypatch):
    """Core lifecycle tests isolate SDK; real writer roundtrips live in writer tests."""

    def write(*, visual, collision, physics, output_path):
        output_path.write_text('#usda 1.0\ndef Xform "Asset" {}\n')
        return {"status": "pass", "mesh_instances": 2, "textures": []}

    monkeypatch.setattr("assets_generator.usd_writer.write_usd", write)
    monkeypatch.setattr(
        "assets_generator.usd_writer.sdk_environment_identity", lambda: {"test": "fake"}
    )


def test_usd_release_preserves_asset_files_and_execution_chain(tmp_path, writer):
    store, source = _source(tmp_path)
    before = store.read_structured(source.release_manifest)
    result = export_usd(
        release=source.release_manifest, store_path=store.root, output_path=tmp_path / "usd"
    )
    assert result.asset_definition == source.asset_definition
    release = store.read_structured(result.release_manifest)
    for name, ref in before["files"].items():
        assert release["files"][name] == ref
    assert store.read_structured(source.release_manifest) == before
    run = store.get_build_run(result.run_id)
    assert run["status"] == "succeeded"
    assert json.loads((result.output_directory / "run.json").read_text())["status"] == "succeeded"
    assert (result.output_directory / "geometry/usd/asset.usda").is_file()
    records = [store.read_structured(ref) for ref in store.find_artifacts("provenance_record")]
    records = [record for record in records if record["run_id"] == result.run_id]
    for ref in [result.usd, result.quality_report, result.release_manifest]:
        record = next(
            record for record in records if record["output_artifact_id"] == ref.artifact_id
        )
        attempt = next(
            item for item in run["node_attempts"] if item["node_id"] == record["node_id"]
        )
        assert to_primitive(ref) in attempt["outputs"].values()


@pytest.mark.parametrize("tamper", ["mass", "frame", "evidence"])
def test_usd_rejects_tampered_physics(tmp_path, writer, tamper):
    store, source = _source(tmp_path)
    raw = store.read_structured(source.release_manifest)
    asset = store.read_structured(source.asset_definition)
    if tamper == "mass":
        asset["physics"]["mass_kg"] += 1
    elif tamper == "frame":
        asset["spatial"]["unit"] = "relative_unit"
    else:
        asset["physics"]["evidence"] = {"artifact_id": source.asset_definition.artifact_id}
    modified = store.persist_structured(
        StructuredValue("asset_definition", "AssetDefinition", "1.0", asset)
    )
    raw["asset_definition"] = to_primitive(modified)
    release = store.persist_structured(StructuredValue("asset_release", "AssetRelease", "1.0", raw))
    with pytest.raises(ContractError):
        export_usd(release=release, store_path=store.root, output_path=tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()


def test_usd_rejects_missing_assignment_evidence(tmp_path, writer):
    store, source = _source(tmp_path)
    raw = store.read_structured(source.release_manifest)
    del raw["files"]["provenance/rigid-body-asset.json"]
    release = store.persist_structured(StructuredValue("asset_release", "AssetRelease", "1.0", raw))
    with pytest.raises(ContractError, match="successful rigid-body"):
        export_usd(release=release, store_path=store.root, output_path=tmp_path / "invalid")


def test_usd_publication_failure_leaves_failed_run_and_no_output(tmp_path, writer, monkeypatch):
    store, source = _source(tmp_path)
    before = set((store.root / "runs").glob("*.json"))

    def fail(*args):
        raise OSError("publication failed")

    monkeypatch.setattr("assets_generator.usd_export._publish_staged_release", fail)
    with pytest.raises(OSError, match="publication failed"):
        export_usd(
            release=source.release_manifest, store_path=store.root, output_path=tmp_path / "failed"
        )
    created = set((store.root / "runs").glob("*.json")) - before
    run = store.get_build_run(created.pop().stem)
    assert run["status"] == "failed"
    assert run["node_attempts"][-1]["error_code"] == "release_failed"
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".failed.*.staged"))


def test_usd_existing_destination_is_not_overwritten(tmp_path, writer):
    store, source = _source(tmp_path)
    output = tmp_path / "exists"
    output.mkdir()
    (output / "sentinel").write_text("keep")
    with pytest.raises(FileExistsError):
        export_usd(release=source.release_manifest, store_path=store.root, output_path=output)
    assert (output / "sentinel").read_text() == "keep"


@pytest.mark.parametrize(
    "feature",
    [
        {"extensionsUsed": ["KHR_materials_unlit"]},
        {"materials": [{"normalTexture": {"index": 0}}]},
        {"materials": [{"alphaMode": "BLEND"}]},
        {"samplers": [{"magFilter": 9729}]},
        {
            "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]}}],
            "meshes": [{"primitives": [{"attributes": {"COLOR_0": 0}, "material": 0}]}],
        },
        {
            "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
            "meshes": [{"primitives": [{"attributes": {"COLOR_0": 0}, "material": 0}]}],
        },
    ],
)
def test_usd_glb_preflight_rejects_lossy_features(feature):
    import struct

    from assets_generator.usd_export import _check_glb_features

    payload = json.dumps({"asset": {"version": "2.0"}, **feature}).encode()
    blob = (
        b"glTF"
        + struct.pack("<II", 2, 20 + len(payload))
        + struct.pack("<II", len(payload), 0x4E4F534A)
        + payload
    )
    with pytest.raises(ContractError):
        _check_glb_features(blob)


def test_usd_real_sdk_core_roundtrip(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdPhysics

    store, source = _source(tmp_path)
    result = export_usd(
        release=source.release_manifest, store_path=store.root, output_path=tmp_path / "real-usd"
    )
    stage = Usd.Stage.Open(str(result.output_directory / "geometry/usd/asset.usda"))
    assert stage.GetDefaultPrim().GetName() == "Asset"
    assert UsdPhysics.MassAPI(stage.GetDefaultPrim()).GetMassAttr().Get() == pytest.approx(12.5)
    assert result.asset_definition == source.asset_definition


def test_usd_cli(tmp_path, writer, monkeypatch, capsys):
    from assets_generator.cli import main

    store, source = _source(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "assets-generator",
            "export-usd",
            "--release",
            source.release_manifest.artifact_id,
            "--store",
            str(store.root),
            "--output",
            str(tmp_path / "cli"),
        ],
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["asset_definition"] == to_primitive(
        source.asset_definition
    )


def test_usd_missing_sdk_records_failed_run(tmp_path, writer, monkeypatch):
    store, source = _source(tmp_path)
    before = set((store.root / "runs").glob("*.json"))

    def missing():
        raise ContractError("OpenUSD SDK is unavailable")

    monkeypatch.setattr("assets_generator.usd_writer.sdk_environment_identity", missing)
    with pytest.raises(ContractError, match="SDK is unavailable"):
        export_usd(
            release=source.release_manifest, store_path=store.root, output_path=tmp_path / "missing"
        )
    created = set((store.root / "runs").glob("*.json")) - before
    run = store.get_build_run(created.pop().stem)
    assert run["status"] == "failed"
    assert run["node_attempts"][0]["status"] == "failed"
    assert not (tmp_path / "missing").exists()


def test_usd_concurrent_destination_is_not_replaced(tmp_path, writer, monkeypatch):
    from assets_generator.rigid_body import _publish_staged_release

    store, source = _source(tmp_path)
    output = tmp_path / "concurrent"

    def competing_publish(staging, target):
        target.mkdir()
        (target / "sentinel").write_text("winner")
        _publish_staged_release(staging, target)

    monkeypatch.setattr("assets_generator.usd_export._publish_staged_release", competing_publish)
    with pytest.raises(FileExistsError):
        export_usd(release=source.release_manifest, store_path=store.root, output_path=output)
    assert (output / "sentinel").read_text() == "winner"
    assert not (output / "asset.json").exists()
    assert not list(tmp_path.glob(".concurrent.*.staged"))


def test_usd_artifact_preserves_validated_canonical_frame(tmp_path, writer, monkeypatch):
    from assets_generator import usd_export

    store, source = _source(tmp_path)
    original_source = usd_export._source

    def custom_frame_source(source_store, release):
        asset, visual, collision, physics, files = original_source(source_store, release)
        # Isolate the export metadata boundary after source validation.
        return asset, visual, collision, {**physics, "frame_id": "asset_variant"}, files

    monkeypatch.setattr(usd_export, "_source", custom_frame_source)
    result = export_usd(
        release=source.release_manifest,
        store_path=store.root,
        output_path=tmp_path / "custom-frame",
    )
    metadata = store.get_manifest(result.usd.artifact_id).identity.identity_metadata
    assert metadata["frame_id"] == "asset_variant"
    assert metadata["unit"] == "meter"

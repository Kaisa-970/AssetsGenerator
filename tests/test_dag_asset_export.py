import io
from dataclasses import replace

import numpy as np
import pytest
import trimesh
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import PortSpec
from assets_generator.dag_adapters import AdapterRegistry
from assets_generator.dag_asset_export import AssetExportAdapter
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.models import BackendNativeFrame, StructuredValue
from assets_generator.operators import (
    assemble_asset,
    canonicalize_glb,
    material_from_glb,
    validate_geometry,
)
from assets_generator.pipeline import (
    PipelineDefinition,
    compile_pipeline,
    load_default_operator_specs,
)
from assets_generator.serialization import to_primitive
from assets_generator.workflow import _persist_provenance


def asset_fixture(store, mode):
    mesh = trimesh.creation.box()
    if mode == "texture":
        mesh.visual = trimesh.visual.texture.TextureVisuals(
            uv=np.zeros((len(mesh.vertices), 2)),
            material=trimesh.visual.material.PBRMaterial(
                baseColorTexture=Image.new("RGB", (2, 2), "red")
            ),
        )
    else:
        mesh.visual.vertex_colors = np.tile([30, 90, 160, 255], (len(mesh.vertices), 1))
    native = store.persist_bytes(
        trimesh.Scene(mesh).export(file_type="glb"),
        kind="triangle_mesh",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={
            "frame_id": "native",
            "unit": "relative_unit",
            "up_axis": "+Z",
            "forward_axis": "+X",
        },
    )
    frame = BackendNativeFrame("native", "right", "+Z", "+X", "declared", "relative_unit")
    canonical = canonicalize_glb(store, native, frame)
    _persist_provenance(
        store,
        run_id="source",
        node_id="canonicalize",
        port_name="mesh",
        artifact=canonical.mesh,
        derived_from=[native],
        operator="canonicalize",
        backend="core",
        backend_version="1",
        parameters={},
        seed=None,
        source="derived",
    )
    report = validate_geometry(store, canonical.mesh, derived_from=native, run_id="source")
    quality = store.persist_structured(
        StructuredValue("quality_report", "QualityReport", "1.0", to_primitive(report))
    )
    asset = assemble_asset(
        canonical.mesh,
        material_from_glb(store, canonical.mesh),
        canonical.result.spatial_info,
        "observation_test",
        quality,
    )
    return asset, report


def run_export(tmp_path, store, asset):
    reference = store.persist_structured(
        StructuredValue("asset_definition", "AssetDefinition", "1.0", to_primitive(asset))
    )
    registry = AdapterRegistry()
    registry.register(AssetExportAdapter())
    plan = registry.bind_plan(
        compile_pipeline(
            PipelineDefinition(
                "export_test",
                "1",
                {
                    "asset": PortSpec(
                        ("asset_definition",),
                        carriers=("artifact_ref",),
                        schema_name="AssetDefinition",
                        schema_version="1.0",
                    )
                },
                {
                    "publish": {
                        "operator": "asset_export@1",
                        "adapter": "asset_export@1",
                        "inputs": {"asset": "pipeline.inputs.asset"},
                    }
                },
            ),
            load_default_operator_specs(),
            require_explicit_joins=True,
        )
    )
    with DagRepository(store, tmp_path / "repo") as repo:
        engine = DagEngine(repo, registry)
        run = engine.drain(engine.create(plan, {"asset": reference}).run_id)
        assert engine.recover(run.run_id).dag.node_states == run.dag.node_states
        return run


@pytest.mark.parametrize("mode", ["texture", "vertex"])
def test_export_preserves_appearance_and_recovers(tmp_path, mode):
    store = LocalArtifactStore(tmp_path / "store")
    asset, _ = asset_fixture(store, mode)
    run = run_export(tmp_path, store, asset)
    assert run.status == "succeeded"
    outputs = run.dag.node_states["publish"].current().outputs
    release = store.read_structured(outputs["release"])
    assert release["files"]["geometry/visual.glb"] == to_primitive(outputs["glb"])
    scene = trimesh.load(
        io.BytesIO(store.blob_path(outputs["glb"]).read_bytes()), file_type="glb", force="scene"
    )
    visual = next(iter(scene.geometry.values())).visual
    if mode == "texture":
        assert visual.material.baseColorTexture.getpixel((0, 0))[:3] == (255, 0, 0)
    else:
        assert np.all(visual.vertex_colors == [30, 90, 160, 255])
    assert (
        store.get_manifest(outputs["glb"].artifact_id).identity.identity_metadata["up_axis"] == "+Y"
    )


def test_export_rejects_failed_qa(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    asset, report = asset_fixture(store, "vertex")
    failed = store.persist_structured(
        StructuredValue(
            "quality_report",
            "QualityReport",
            "1.0",
            to_primitive(replace(report, overall_status="fail")),
        )
    )
    run = run_export(tmp_path, store, replace(asset, quality_report_ids=[failed.artifact_id]))
    assert run.status == "failed"
    assert not store.find_artifacts("asset_release")


def test_export_rejects_spatial_mismatch(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    asset, _ = asset_fixture(store, "vertex")
    mismatched = replace(asset, spatial=replace(asset.spatial, canonical_frame_id="another_frame"))
    assert run_export(tmp_path, store, mismatched).status == "failed"
    assert not store.find_artifacts("asset_release")


def test_export_rejects_report_for_other_mesh(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    first, _ = asset_fixture(store, "vertex")
    second, _ = asset_fixture(store, "texture")
    mismatched = replace(second, quality_report_ids=first.quality_report_ids)
    assert run_export(tmp_path, store, mismatched).status == "failed"
    assert not store.find_artifacts("asset_release")

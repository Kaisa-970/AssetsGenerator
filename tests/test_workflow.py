from __future__ import annotations

import io
import json

import pytest
import trimesh
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.backend_registry import BackendRegistry, ResolvedPlan, resolve_plan
from assets_generator.contracts import ContractError
from assets_generator.models import (
    SCHEMA_VERSION,
    ArtifactRef,
    BackendNativeFrame,
    PBRMaterial,
    StructuredValue,
)
from assets_generator.operators import SegmentationOutput, ShapeOutput
from assets_generator.serialization import to_primitive
from assets_generator.workflow import build_image_asset


class ContractBackend:
    def generate(self, store, rgba, *, seed=42, pipeline_type="512") -> ShapeOutput:
        scene = trimesh.Scene(trimesh.creation.box(extents=[1.0, 2.0, 3.0]))
        data = scene.export(file_type="glb")
        assert isinstance(data, bytes)
        mesh = store.persist_bytes(
            data,
            kind="triangle_mesh",
            schema_name="glTF",
            schema_version="2.0",
            identity_metadata={
                "frame_id": "contract_native",
                "unit": "relative_unit",
                "up_axis": "+Y",
                "forward_axis": None,
            },
        )
        frame = BackendNativeFrame(
            "contract_native", "right", "+Y", None, "unknown", "relative_unit"
        )
        material = PBRMaterial([1.0, 1.0, 1.0, 1.0])
        return ShapeOutput(
            mesh,
            StructuredValue("pbr_material", "PBRMaterial", SCHEMA_VERSION, to_primitive(material)),
            StructuredValue(
                "backend_native_frame",
                "BackendNativeFrame",
                SCHEMA_VERSION,
                to_primitive(frame),
            ),
            {"backend_version": "test"},
        )


class FailingBackend:
    def generate(self, store, rgba, *, seed=42, pipeline_type="512") -> ShapeOutput:
        raise RuntimeError("backend exploded")


class InvalidUnitBackend(ContractBackend):
    def generate(self, store, rgba, *, seed=42, pipeline_type="512") -> ShapeOutput:
        value = super().generate(store, rgba, seed=seed, pipeline_type=pipeline_type)
        return ShapeOutput(
            value.mesh,
            value.material,
            StructuredValue(
                "backend_native_frame",
                "BackendNativeFrame",
                SCHEMA_VERSION,
                {**value.native_frame.value, "unit": "millimeter"},
            ),
            value.backend_metadata,
        )


class MismatchedSpatialBackend(ContractBackend):
    def generate(self, store, rgba, *, seed=42, pipeline_type="512") -> ShapeOutput:
        value = super().generate(store, rgba, seed=seed, pipeline_type=pipeline_type)
        return ShapeOutput(
            value.mesh,
            value.material,
            StructuredValue(
                "backend_native_frame",
                "BackendNativeFrame",
                SCHEMA_VERSION,
                {**value.native_frame.value, "frame_id": "different_native"},
            ),
            value.backend_metadata,
        )


class WrongKindBackend(ContractBackend):
    def generate(self, store, rgba, *, seed=42, pipeline_type="512") -> ShapeOutput:
        value = super().generate(store, rgba, seed=seed, pipeline_type=pipeline_type)
        wrong = store.persist_bytes(
            store.blob_path(value.mesh).read_bytes(),
            kind="point_cloud",
            schema_name="glTF",
            schema_version="2.0",
            identity_metadata={"frame_id": "contract_native", "unit": "relative_unit"},
        )
        return ShapeOutput(wrong, value.material, value.native_frame, value.backend_metadata)


class ArtifactNativeFrameBackend(ContractBackend):
    def generate(self, store, rgba, *, seed=42, pipeline_type="512") -> ShapeOutput:
        value = super().generate(store, rgba, seed=seed, pipeline_type=pipeline_type)
        return ShapeOutput(value.mesh, value.material, value.mesh, value.backend_metadata)


class DanglingMeshBackend(ContractBackend):
    def generate(self, store, rgba, *, seed=42, pipeline_type="512") -> ShapeOutput:
        value = super().generate(store, rgba, seed=seed, pipeline_type=pipeline_type)
        return ShapeOutput(
            ArtifactRef("sha256:" + "f" * 64),
            value.material,
            value.native_frame,
            value.backend_metadata,
        )


class ContractSegmentationBackend:
    def __init__(self, *, cache_hit: bool = False) -> None:
        self.calls = 0
        self.cache_hit = cache_hit

    def segment(self, store, image: ArtifactRef) -> SegmentationOutput:
        self.calls += 1
        with Image.open(store.blob_path(image)) as source:
            output = io.BytesIO()
            Image.new("L", source.size, 255).save(output, format="PNG")
        mask = store.persist_bytes(
            output.getvalue(),
            kind="binary_mask",
            schema_name="png",
            schema_version="1.0",
        )
        return SegmentationOutput(
            mask,
            StructuredValue(
                "segmentation_result",
                "SegmentationResult",
                SCHEMA_VERSION,
                {"source": "generated", "threshold": 128},
            ),
            {"backend": "contract", "backend_version": "test"},
            self.cache_hit,
        )


def test_phase1_workflow_materializes_release(tmp_path) -> None:
    image_path = tmp_path / "image.png"
    mask_path = tmp_path / "mask.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    Image.new("L", (8, 8), 255).save(mask_path)
    output = tmp_path / "release"

    result = build_image_asset(
        image_path=image_path,
        mask_path=mask_path,
        store_path=tmp_path / "store",
        output_path=output,
        backend=ContractBackend(),  # type: ignore[arg-type]
        asset_name="box",
    )

    assert result.output_directory == output
    release = json.loads((output / "release.json").read_text(encoding="utf-8"))
    for relative_path in release["files"]:
        assert (output / relative_path).is_file()
    loaded = trimesh.load(
        io.BytesIO((output / "geometry/visual.glb").read_bytes()), file_type="glb"
    )
    assert isinstance(loaded, trimesh.Scene)
    report = json.loads((output / "qa/quality-report.json").read_text(encoding="utf-8"))
    assert report["overall_status"] == "warn"
    run = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert run["status"] == "succeeded"
    assert run["pipeline_name"] == "image_asset_v2"
    assert run["node_attempts"][0]["node_id"] == "resolve_mask"
    assert all(
        {"attempt", "operator", "status", "execution_mode", "started_at", "finished_at", "outputs"}
        <= attempt.keys()
        for attempt in run["node_attempts"]
    )
    store = LocalArtifactStore(tmp_path / "store")
    assert run == store.get_build_run(result.run_id)
    assert run["node_attempts"][-1]["node_id"] == "materialize_release"


def test_workflow_uses_backend_name_from_resolved_plan(tmp_path) -> None:
    image_path = tmp_path / "image.png"
    mask_path = tmp_path / "mask.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    Image.new("L", (8, 8), 255).save(mask_path)
    registry = BackendRegistry()
    registry.register(
        name="contract_shape",
        operator="shape_generation@1",
        backend_version="test",
        implementation=ContractBackend(),
    )
    from assets_generator.pipeline import load_default_operator_specs, load_default_pipeline

    plan = resolve_plan(
        load_default_pipeline(),
        registry,
        operator_specs=load_default_operator_specs(),
        backend_overrides={"generate_shape": "contract_shape"},
    )
    output = tmp_path / "release"

    build_image_asset(
        image_path=image_path,
        mask_path=mask_path,
        store_path=tmp_path / "store",
        output_path=output,
        resolved_plan=plan,
    )

    run = json.loads((output / "run.json").read_text(encoding="utf-8"))
    shape_attempt = next(
        item for item in run["node_attempts"] if item["node_id"] == "generate_shape"
    )
    assert shape_attempt["backend"] == "contract_shape"
    assert run["resolved_backends"] == {"generate_shape": "contract_shape"}
    assert run["resolved_plan_contract_digest"] == plan.contract_digest
    assert run["resolved_backend_versions"] == {"generate_shape": "test"}
    provenance = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((output / "provenance").glob("*.json"))
    ]
    shape_record = next(item for item in provenance if item["node_id"] == "generate_shape")
    assert shape_record["backend"] == "contract_shape"


def test_workflow_rejects_ambiguous_backend_configuration(tmp_path) -> None:
    image_path = tmp_path / "image.png"
    mask_path = tmp_path / "mask.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    Image.new("L", (8, 8), 255).save(mask_path)

    with pytest.raises(ContractError, match="configure exactly one"):
        build_image_asset(
            image_path=image_path,
            mask_path=mask_path,
            store_path=tmp_path / "store",
            output_path=tmp_path / "release",
            backend=ContractBackend(),  # type: ignore[arg-type]
            backend_registry=BackendRegistry(),
        )


def test_workflow_rejects_backend_name_without_inline_backend(tmp_path) -> None:
    image_path = tmp_path / "image.png"
    mask_path = tmp_path / "mask.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    Image.new("L", (8, 8), 255).save(mask_path)

    with pytest.raises(ContractError, match="backend_name requires"):
        build_image_asset(
            image_path=image_path,
            mask_path=mask_path,
            store_path=tmp_path / "store",
            output_path=tmp_path / "release",
            backend_name="unused",
        )


def test_workflow_rejects_resolved_plan_for_different_contract(tmp_path) -> None:
    image_path = tmp_path / "image.png"
    mask_path = tmp_path / "mask.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    Image.new("L", (8, 8), 255).save(mask_path)
    plan = ResolvedPlan("image_asset_v2", "2", "sha256:stale", {})

    with pytest.raises(ContractError, match="contract digest"):
        build_image_asset(
            image_path=image_path,
            mask_path=mask_path,
            store_path=tmp_path / "store",
            output_path=tmp_path / "release",
            resolved_plan=plan,
        )


def test_backend_failure_persists_failed_build_run(tmp_path) -> None:
    image_path = tmp_path / "image.png"
    mask_path = tmp_path / "mask.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    Image.new("L", (8, 8), 255).save(mask_path)
    store_path = tmp_path / "store"

    with pytest.raises(RuntimeError, match="backend exploded"):
        build_image_asset(
            image_path=image_path,
            mask_path=mask_path,
            store_path=store_path,
            output_path=tmp_path / "release",
            backend=FailingBackend(),  # type: ignore[arg-type]
        )

    store = LocalArtifactStore(store_path)
    indexes = list((store.root / "runs").glob("*.json"))
    assert len(indexes) == 1
    run = store.get_build_run(indexes[0].stem)
    assert run["status"] == "failed"
    assert run["node_attempts"][-1]["node_id"] == "generate_shape"
    assert run["node_attempts"][-1]["status"] == "failed"
    assert run["node_attempts"][-1]["error_code"] == "internal_error"
    assert run["resolved_plan_contract_digest"]
    assert run["resolved_backend_versions"] == {"generate_shape": "configured"}


def test_workflow_rejects_unknown_native_unit_at_backend_node(tmp_path) -> None:
    image_path = tmp_path / "image.png"
    mask_path = tmp_path / "mask.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    Image.new("L", (8, 8), 255).save(mask_path)
    store_path = tmp_path / "store"

    with pytest.raises(ContractError, match="invalid native frame unit"):
        build_image_asset(
            image_path=image_path,
            mask_path=mask_path,
            store_path=store_path,
            output_path=tmp_path / "release",
            backend=InvalidUnitBackend(),
        )

    run_index = next((store_path / "runs").glob("*.json"))
    attempt = LocalArtifactStore(store_path).get_build_run(run_index.stem)["node_attempts"][-1]
    assert attempt["node_id"] == "generate_shape"
    assert attempt["error_code"] == "contract_error"


def test_workflow_rejects_mesh_native_frame_mismatch_at_backend_node(tmp_path) -> None:
    image_path = tmp_path / "image.png"
    mask_path = tmp_path / "mask.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    Image.new("L", (8, 8), 255).save(mask_path)
    store_path = tmp_path / "store"

    with pytest.raises(ContractError, match="mesh frame_id does not match"):
        build_image_asset(
            image_path=image_path,
            mask_path=mask_path,
            store_path=store_path,
            output_path=tmp_path / "release",
            backend=MismatchedSpatialBackend(),
        )

    run_index = next((store_path / "runs").glob("*.json"))
    attempt = LocalArtifactStore(store_path).get_build_run(run_index.stem)["node_attempts"][-1]
    assert attempt["node_id"] == "generate_shape"
    assert attempt["error_code"] == "contract_error"


@pytest.mark.parametrize(
    ("backend", "message"),
    [
        (ArtifactNativeFrameBackend(), "native_frame rejects carrier artifact_ref"),
        (DanglingMeshBackend(), "artifact has invalid digest"),
    ],
)
def test_workflow_records_shape_output_contract_failures(tmp_path, backend, message) -> None:
    image_path = tmp_path / "image.png"
    mask_path = tmp_path / "mask.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    Image.new("L", (8, 8), 255).save(mask_path)
    store_path = tmp_path / "store"

    with pytest.raises(ContractError, match=message):
        build_image_asset(
            image_path=image_path,
            mask_path=mask_path,
            store_path=store_path,
            output_path=tmp_path / "release",
            backend=backend,
        )

    run_index = next((store_path / "runs").glob("*.json"))
    attempt = LocalArtifactStore(store_path).get_build_run(run_index.stem)["node_attempts"][-1]
    assert attempt["node_id"] == "generate_shape"
    assert attempt["error_code"] == "contract_error"


def test_inline_backend_uses_explicit_audit_name(tmp_path) -> None:
    image_path = tmp_path / "image.png"
    mask_path = tmp_path / "mask.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    Image.new("L", (8, 8), 255).save(mask_path)
    output = tmp_path / "release"

    build_image_asset(
        image_path=image_path,
        mask_path=mask_path,
        store_path=tmp_path / "store",
        output_path=output,
        backend=ContractBackend(),  # type: ignore[arg-type]
        backend_name="contract_shape",
    )

    run = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert run["resolved_backends"] == {"generate_shape": "contract_shape"}


def test_release_materialization_failure_is_atomic(tmp_path, monkeypatch) -> None:
    image_path = tmp_path / "image.png"
    mask_path = tmp_path / "mask.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    Image.new("L", (8, 8), 255).save(mask_path)
    output = tmp_path / "release"

    with monkeypatch.context() as context:
        context.setattr(
            "assets_generator.workflow.shutil.copyfile",
            lambda *args: (_ for _ in ()).throw(OSError("copy failed")),
        )
        with pytest.raises(OSError, match="copy failed"):
            build_image_asset(
                image_path=image_path,
                mask_path=mask_path,
                store_path=tmp_path / "store",
                output_path=output,
                backend=ContractBackend(),  # type: ignore[arg-type]
            )

    assert not output.exists()
    assert not list(tmp_path.glob(".release.*.tmp"))
    build_image_asset(
        image_path=image_path,
        mask_path=mask_path,
        store_path=tmp_path / "store",
        output_path=output,
        backend=ContractBackend(),  # type: ignore[arg-type]
    )
    assert (output / "release.json").is_file()


def test_release_failure_is_a_separate_attempt(tmp_path, monkeypatch) -> None:
    image_path = tmp_path / "image.png"
    mask_path = tmp_path / "mask.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    Image.new("L", (8, 8), 255).save(mask_path)
    output = tmp_path / "release"
    monkeypatch.setattr(
        "assets_generator.workflow.shutil.copyfile",
        lambda *args: (_ for _ in ()).throw(OSError("copy failed")),
    )
    with pytest.raises(OSError):
        build_image_asset(
            image_path=image_path,
            mask_path=mask_path,
            store_path=tmp_path / "store",
            output_path=output,
            backend=ContractBackend(),  # type: ignore[arg-type]
        )
    store = LocalArtifactStore(tmp_path / "store")
    run = store.get_build_run(next((store.root / "runs").glob("*.json")).stem)
    assert run["node_attempts"][-1]["node_id"] == "materialize_release"
    assert run["node_attempts"][-1]["status"] == "failed"
    assert run["node_attempts"][-1]["error_code"] == "release_failed"


def test_build_validates_actual_node_outputs_against_packaged_contract(
    tmp_path,
) -> None:
    image_path = tmp_path / "image.png"
    mask_path = tmp_path / "mask.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    Image.new("L", (8, 8), 255).save(mask_path)

    with pytest.raises(ContractError, match="rejects kind point_cloud"):
        build_image_asset(
            image_path=image_path,
            mask_path=mask_path,
            store_path=tmp_path / "store",
            output_path=tmp_path / "release",
            backend=WrongKindBackend(),  # type: ignore[arg-type]
        )

    store = LocalArtifactStore(tmp_path / "store")
    index = next((store.root / "runs").glob("*.json"))
    run = store.get_build_run(index.stem)
    assert run["status"] == "failed"
    assert run["node_attempts"][-1]["status"] == "failed"
    assert run["node_attempts"][-1]["error_code"] == "contract_error"


def test_workflow_generates_mask_when_optional_input_is_absent(tmp_path) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    segmentation = ContractSegmentationBackend()
    output = tmp_path / "release"

    build_image_asset(
        image_path=image_path,
        mask_path=None,
        store_path=tmp_path / "store",
        output_path=output,
        backend=ContractBackend(),  # type: ignore[arg-type]
        segmentation_backend=segmentation,  # type: ignore[arg-type]
    )

    assert segmentation.calls == 1
    run = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert "source_mask" not in run["inputs"]
    assert run["node_attempts"][0]["backend"] == "birefnet_lite"
    provenance = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((output / "provenance").glob("*.json"))
    ]
    segmentation_record = next(item for item in provenance if item["node_id"] == "resolve_mask")
    assert segmentation_record["source"] == "generated"


def test_segmentation_cache_hit_is_recorded_in_node_attempt(tmp_path) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    output = tmp_path / "release"

    build_image_asset(
        image_path=image_path,
        mask_path=None,
        store_path=tmp_path / "store",
        output_path=output,
        backend=ContractBackend(),  # type: ignore[arg-type]
        segmentation_backend=ContractSegmentationBackend(cache_hit=True),  # type: ignore[arg-type]
    )

    run = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert run["node_attempts"][0]["execution_mode"] == "cache_hit"


def test_provided_mask_skips_segmentation_backend(tmp_path) -> None:
    image_path = tmp_path / "image.png"
    mask_path = tmp_path / "mask.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    Image.new("L", (8, 8), 255).save(mask_path)
    segmentation = ContractSegmentationBackend()

    build_image_asset(
        image_path=image_path,
        mask_path=mask_path,
        store_path=tmp_path / "store",
        output_path=tmp_path / "release",
        backend=ContractBackend(),  # type: ignore[arg-type]
        segmentation_backend=segmentation,  # type: ignore[arg-type]
    )

    assert segmentation.calls == 0


def test_provided_mask_is_validated_by_resolve_mask_node(tmp_path) -> None:
    image_path = tmp_path / "image.png"
    mask_path = tmp_path / "mask.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    Image.new("L", (8, 8), 127).save(mask_path)
    store_path = tmp_path / "store"

    with pytest.raises(RuntimeError, match="values other than 0 and 255"):
        build_image_asset(
            image_path=image_path,
            mask_path=mask_path,
            store_path=store_path,
            output_path=tmp_path / "release",
            backend=ContractBackend(),  # type: ignore[arg-type]
        )

    store = LocalArtifactStore(store_path)
    run_id = next((store.root / "runs").glob("*.json")).stem
    run = store.get_build_run(run_id)
    assert run["node_attempts"][-1]["node_id"] == "resolve_mask"
    assert run["node_attempts"][-1]["error_code"] == "output_invalid"


@pytest.mark.parametrize("appearance", ["texture", "vertex_colors", "multiple_materials"])
def test_image_workflow_preserves_mesh_appearance(tmp_path, appearance) -> None:
    import numpy as np

    scene = trimesh.Scene()
    for index in range(2 if appearance == "multiple_materials" else 1):
        mesh = trimesh.creation.box(extents=[1.0 + index, 2.0, 3.0])
        if appearance == "vertex_colors":
            colors = np.array(
                [[20 + i * 20, 200 - i * 10, 40 + i * 15, 255] for i in range(8)],
                dtype=np.uint8,
            )
            mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, vertex_colors=colors)
        else:
            pixels = np.array(
                [
                    [[12, 34, 56, 255], [240, 20, 60, 255]],
                    [[50, 180, 70, 255], [90, 100, 220, 255]],
                ],
                dtype=np.uint8,
            )
            material = trimesh.visual.material.PBRMaterial(
                name=f"material_{index}",
                baseColorFactor=[180, 100 + index * 90, 60, 255],
                baseColorTexture=Image.fromarray(pixels) if appearance == "texture" else None,
                metallicFactor=0.2 + index * 0.3,
                roughnessFactor=0.7 - index * 0.2,
            )
            mesh.visual = trimesh.visual.texture.TextureVisuals(
                uv=np.array([[i % 2, (i // 2) % 2] for i in range(8)], dtype=float),
                material=material,
            )
        mesh.apply_translation([index * 3, 0, 0])
        scene.add_geometry(mesh, geom_name=f"part_{index}", node_name=f"node_{index}")
    source_glb = scene.export(file_type="glb")
    expected = trimesh.load(io.BytesIO(source_glb), file_type="glb", force="scene")

    class AppearanceBackend(ContractBackend):
        def generate(self, store, rgba, *, seed=42, pipeline_type="512"):
            value = super().generate(store, rgba, seed=seed, pipeline_type=pipeline_type)
            mesh_ref = store.persist_bytes(
                source_glb,
                kind="triangle_mesh",
                schema_name="glTF",
                schema_version="2.0",
                identity_metadata={
                    "frame_id": "contract_native",
                    "unit": "relative_unit",
                    "up_axis": "+Y",
                    "forward_axis": None,
                },
            )
            return ShapeOutput(mesh_ref, value.material, value.native_frame, value.backend_metadata)

    image_path, mask_path = tmp_path / "image.png", tmp_path / "mask.png"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(image_path)
    Image.new("L", (8, 8), 255).save(mask_path)
    output = tmp_path / "release"
    build_image_asset(
        image_path=image_path,
        mask_path=mask_path,
        store_path=tmp_path / "store",
        output_path=output,
        backend=AppearanceBackend(),
        asset_name="appearance",
    )
    actual = trimesh.load(output / "geometry/visual.glb", force="scene")
    assert set(actual.geometry) == set(expected.geometry)
    for name, original in expected.geometry.items():
        exported = actual.geometry[name]
        assert exported.visual.kind == original.visual.kind
        np.testing.assert_array_equal(exported.faces, original.faces)
        if appearance == "vertex_colors":
            np.testing.assert_array_equal(
                exported.visual.vertex_colors, original.visual.vertex_colors
            )
        else:
            before, after = original.visual.material, exported.visual.material
            assert after.name == before.name
            np.testing.assert_array_equal(after.baseColorFactor, before.baseColorFactor)
            assert after.metallicFactor == before.metallicFactor
            assert after.roughnessFactor == before.roughnessFactor
            np.testing.assert_allclose(exported.visual.uv, original.visual.uv)
            if appearance == "texture":
                np.testing.assert_array_equal(
                    np.asarray(after.baseColorTexture), np.asarray(before.baseColorTexture)
                )
    for node in expected.graph.nodes_geometry:
        assert actual.graph[node][1] == expected.graph[node][1]
    records = [json.loads(path.read_text()) for path in (output / "provenance").glob("*.json")]
    export_record = next(record for record in records if record["operator"] == "export")
    assert export_record["parameters"]["appearance_mode"] == "preserve_mesh"

from __future__ import annotations

import io
import json

import pytest
import trimesh
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
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

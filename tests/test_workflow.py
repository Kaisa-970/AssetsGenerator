from __future__ import annotations

import io
import json

import pytest
import trimesh
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.models import SCHEMA_VERSION, BackendNativeFrame, PBRMaterial, StructuredValue
from assets_generator.operators import ShapeOutput
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
    assert run["node_attempts"][-1]["error_code"] == "RuntimeError"


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
    assert run["node_attempts"][-1]["error_code"] == "ContractError"

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.errors import ErrorCode, PipelineError
from assets_generator.models import (
    SCHEMA_VERSION,
    ArtifactRef,
    BackendNativeFrame,
    ComponentProvenance,
    PBRMaterial,
    ProvenanceRecord,
    SemanticInfo,
)
from assets_generator.operators import (
    BiRefNetSegmentationBackend,
    Trellis2Backend,
    TripoSRBackend,
    assemble_asset,
    canonicalize_glb,
    validate_binary_mask,
    validate_geometry,
)
from assets_generator.serialization import canonical_json_bytes


def _box_glb() -> bytes:
    scene = trimesh.Scene(trimesh.creation.box(extents=[2.0, 1.0, 4.0]))
    result = scene.export(file_type="glb")
    assert isinstance(result, bytes)
    return result


def test_assemble_asset_preserves_component_provenance(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    mesh = store.persist_bytes(
        _box_glb(),
        kind="triangle_mesh",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={"frame_id": "asset_canonical", "unit": "relative_unit"},
    )
    quality = store.persist_bytes(
        b"{}", kind="quality_report", schema_name="QualityReport", schema_version="1.0"
    )
    component = ComponentProvenance("visible", mesh, "reconstructed", ["provenance_1"])

    asset = assemble_asset(
        mesh,
        PBRMaterial([1.0, 1.0, 1.0, 1.0]),
        object(),
        "observation_1",
        quality,
        components=[component],
    )

    assert asset.component_provenance == [component]


@pytest.mark.parametrize(
    "semantics",
    [
        SemanticInfo(1, "user"),  # type: ignore[arg-type]
        SemanticInfo("chair", 1),  # type: ignore[arg-type]
        SemanticInfo("chair", "unverified"),
    ],
)
def test_assemble_asset_rejects_invalid_semantics(tmp_path, semantics) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    mesh = store.persist_bytes(
        _box_glb(),
        kind="triangle_mesh",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={"frame_id": "asset_canonical", "unit": "relative_unit"},
    )
    quality = store.persist_bytes(
        b"{}", kind="quality_report", schema_name="QualityReport", schema_version="1.0"
    )

    with pytest.raises(ValueError, match="SemanticInfo"):
        assemble_asset(
            mesh,
            PBRMaterial([1.0, 1.0, 1.0, 1.0]),
            object(),
            "observation_1",
            quality,
            semantics=semantics,
        )


def test_canonicalization_creates_new_artifact_and_loadable_glb(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    native = store.persist_bytes(
        _box_glb(),
        kind="triangle_mesh",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={
            "frame_id": "trellis2_glb_native",
            "unit": "relative_unit",
            "up_axis": "+Y",
            "forward_axis": None,
        },
    )
    result = canonicalize_glb(
        store,
        native,
        BackendNativeFrame("trellis2_glb_native", "right", "+Y", None, "unknown", "relative_unit"),
    )

    assert result.mesh != native
    assert result.result.spatial_info.canonical_frame_id == "asset_canonical"
    assert np.isclose(max(np.ptp(result.result.vertices, axis=0)), 1.0)
    reloaded = trimesh.load(io.BytesIO(store.blob_path(result.mesh).read_bytes()), file_type="glb")
    assert isinstance(reloaded, trimesh.Scene)
    assert reloaded.geometry


def test_geometry_qa_warns_for_relative_scale_and_skips_optional_checks(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    mesh = store.persist_bytes(
        _box_glb(),
        kind="triangle_mesh",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={
            "frame_id": "asset_canonical",
            "unit": "relative_unit",
            "up_axis": "+Z",
            "forward_axis": "+X",
        },
    )
    native = store.persist_bytes(
        _box_glb(),
        kind="triangle_mesh",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={"frame_id": "native", "unit": "relative_unit"},
    )
    record = ProvenanceRecord(
        "provenance",
        "output",
        mesh.artifact_id,
        [native.artifact_id],
        "canonicalize",
        "1",
        "core",
        "test",
        None,
        None,
        {},
        None,
        "run",
        "canonicalize",
        1,
        "derived",
    )
    store.persist_bytes(
        canonical_json_bytes(record),
        kind="provenance_record",
        schema_name="ProvenanceRecord",
        schema_version=SCHEMA_VERSION,
        identity_metadata={"media_type": "application/json"},
    )
    report = validate_geometry(store, mesh, derived_from=native, run_id="run")
    checks = {check.check_id: check for check in report.checks}

    assert report.overall_status == "warn"
    assert checks["metric_scale"].status == "warn"
    assert checks["collision_loadable"].status == "skipped"
    assert checks["render_back"].status == "skipped"


def test_binary_mask_rejects_empty_foreground(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    image_buffer = io.BytesIO()
    mask_buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(image_buffer, format="PNG")
    Image.new("L", (4, 4), 0).save(mask_buffer, format="PNG")
    image = store.persist_bytes(
        image_buffer.getvalue(), kind="rgb_image", schema_name="png", schema_version="1.0"
    )
    mask = store.persist_bytes(
        mask_buffer.getvalue(), kind="binary_mask", schema_name="png", schema_version="1.0"
    )

    with pytest.raises(PipelineError, match="no foreground pixels") as captured:
        validate_binary_mask(store, image, mask)

    assert captured.value.code == ErrorCode.OUTPUT_INVALID


def test_geometry_qa_rejects_provenance_for_wrong_output(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    native = store.persist_bytes(
        _box_glb(),
        kind="triangle_mesh",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={"frame_id": "native", "unit": "relative_unit"},
    )
    canonical = store.persist_bytes(
        _box_glb(),
        kind="triangle_mesh",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={"frame_id": "asset_canonical", "unit": "relative_unit"},
    )
    wrong = ProvenanceRecord(
        "provenance",
        "output",
        native.artifact_id,
        [native.artifact_id],
        "canonicalize",
        "1",
        "core",
        "test",
        None,
        None,
        {},
        None,
        "run",
        "canonicalize",
        1,
        "derived",
    )
    store.persist_bytes(
        canonical_json_bytes(wrong),
        kind="provenance_record",
        schema_name="ProvenanceRecord",
        schema_version=SCHEMA_VERSION,
        identity_metadata={"media_type": "application/json"},
    )

    report = validate_geometry(store, canonical, derived_from=native)
    checks = {check.check_id: check for check in report.checks}
    assert checks["mandatory_provenance"].status == "fail"
    assert report.overall_status == "fail"


def test_backend_protocol_uses_absolute_paths_across_working_directories(
    tmp_path, monkeypatch
) -> None:
    store = LocalArtifactStore(tmp_path / "relative-store")
    rgba = store.persist_bytes(
        b"rgba",
        kind="rgba_image",
        schema_name="test",
        schema_version="1.0",
    )
    runner = (Path(__file__).parent / "fixtures" / "backend_path_runner.py").resolve()
    backend = Trellis2Backend(Path(sys.executable), tmp_path)
    monkeypatch.setattr(backend, "_runner_path", lambda: runner)

    result = backend.generate(store, ArtifactRef(rgba.artifact_id))

    assert result.mesh.artifact_id
    assert Path(result.backend_metadata["request_path"]).is_absolute()


def test_backend_timeout_is_reported(tmp_path, monkeypatch) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    rgba = store.persist_bytes(b"rgba", kind="rgba_image", schema_name="test", schema_version="1.0")
    backend = Trellis2Backend(Path(sys.executable), tmp_path, timeout_seconds=0.01)

    runner = tmp_path / "slow_runner.py"
    runner.write_text("import time; time.sleep(1)\n", encoding="utf-8")
    monkeypatch.setattr(backend, "_runner_path", lambda: runner)

    with pytest.raises(PipelineError) as captured:
        backend.generate(store, rgba)
    assert captured.value.code == ErrorCode.BACKEND_TIMEOUT


def _rgba_artifact(store: LocalArtifactStore, tmp_path: Path) -> ArtifactRef:
    image_path = tmp_path / "rgba.png"
    Image.new("RGBA", (8, 8), (20, 40, 60, 0)).save(image_path)
    return store.persist_bytes(
        image_path.read_bytes(),
        kind="rgba_image",
        schema_name="png",
        schema_version="1.0",
    )


def test_triposr_backend_validates_configuration(tmp_path) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        TripoSRBackend(Path(sys.executable), tmp_path, timeout_seconds=0)
    with pytest.raises(ValueError, match="chunk_size"):
        TripoSRBackend(Path(sys.executable), tmp_path, chunk_size=0)
    with pytest.raises(ValueError, match="mc_resolution"):
        TripoSRBackend(Path(sys.executable), tmp_path, mc_resolution=0)
    with pytest.raises(ValueError, match="foreground_ratio"):
        TripoSRBackend(Path(sys.executable), tmp_path, foreground_ratio=0)
    with pytest.raises(ValueError, match="foreground_ratio"):
        TripoSRBackend(Path(sys.executable), tmp_path, foreground_ratio=1.01)


def test_triposr_runner_import_does_not_require_model_dependencies() -> None:
    runner = Path(__file__).parents[1] / "src/assets_generator/backends/triposr_runner.py"
    result = subprocess.run(
        [sys.executable, "-S", str(runner)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "usage: triposr_runner.py" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def test_triposr_backend_publishes_validated_native_mesh(
    tmp_path, monkeypatch, triposr_environment
) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    rgba = _rgba_artifact(store, tmp_path)
    runner = (Path(__file__).parent / "fixtures" / "triposr_path_runner.py").resolve()
    repo, evidence = triposr_environment()
    backend = TripoSRBackend(
        Path(sys.executable),
        repo,
        model=tmp_path / "model",
        chunk_size=4096,
        mc_resolution=128,
        foreground_ratio=0.75,
        frame_validation=evidence,
    )
    monkeypatch.setattr(backend, "_runner_path", lambda: runner)

    result = backend.generate(store, rgba, seed=17, pipeline_type="1024")

    manifest = store.get_manifest(result.mesh.artifact_id)
    assert manifest.identity.kind == "triangle_mesh"
    assert manifest.identity.identity_metadata == {
        "media_type": "model/gltf-binary",
        "frame_id": "triposr_glb_native",
        "unit": "relative_unit",
        "up_axis": "+Z",
        "forward_axis": None,
    }
    assert result.native_frame.value == {
        "frame_id": "triposr_glb_native",
        "handedness": "right",
        "up_axis": "+Z",
        "forward_axis": None,
        "forward_status": "unknown",
        "unit": "relative_unit",
    }
    assert result.material.value["base_color_factor"] == [1.0, 1.0, 1.0, 1.0]
    metadata = result.backend_metadata
    assert metadata["seed"] == 17
    assert metadata["seed_effective"] is False
    assert metadata["pipeline_type"] == "1024"
    assert metadata["pipeline_type_effective"] is False
    assert metadata["chunk_size"] == 4096
    assert metadata["mc_resolution"] == 128
    assert metadata["foreground_ratio"] == 0.75
    assert metadata["validated_vertex_count"] == 4
    assert metadata["validated_face_count"] == 4
    assert metadata["native_frame_validation"] == "triposr-marching-cubes-glb-roundtrip-v1"
    assert metadata["native_frame_validation_evidence_digest"].startswith("sha256:")
    assert metadata["native_frame_backend_source_digest"].startswith("sha256:")
    assert Path(metadata["request"]["repo"]).is_absolute()
    assert Path(metadata["request"]["input_image"]).is_absolute()
    assert Path(metadata["request"]["output_glb"]).is_absolute()
    scene = trimesh.load(store.blob_path(result.mesh), file_type="glb", force="scene")
    assert isinstance(scene, trimesh.Scene)
    assert np.allclose(scene.bounds, [[0.0, 0.0, 0.0], [4.0, 2.0, 1.0]])


def test_triposr_backend_rejects_invalid_response(
    tmp_path, monkeypatch, triposr_environment
) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    rgba = _rgba_artifact(store, tmp_path)
    runner = tmp_path / "bad_response.py"
    runner.write_text(
        "from pathlib import Path\n"
        "import json, sys, trimesh\n"
        "request = json.loads(Path(sys.argv[1]).read_text())\n"
        "trimesh.creation.box().export(request['output_glb'])\n"
        "Path(sys.argv[2]).write_text(json.dumps({'backend': 'triposr'}))\n",
        encoding="utf-8",
    )
    repo, evidence = triposr_environment()
    backend = TripoSRBackend(Path(sys.executable), repo, frame_validation=evidence)
    monkeypatch.setattr(backend, "_runner_path", lambda: runner)

    with pytest.raises(PipelineError) as captured:
        backend.generate(store, rgba)
    assert captured.value.code == ErrorCode.OUTPUT_INVALID
    assert "missing fields" in str(captured.value)


def test_triposr_backend_requires_declared_outputs(
    tmp_path, monkeypatch, triposr_environment
) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    rgba = _rgba_artifact(store, tmp_path)
    runner = tmp_path / "no_outputs.py"
    runner.write_text("pass\n", encoding="utf-8")
    repo, evidence = triposr_environment()
    backend = TripoSRBackend(Path(sys.executable), repo, frame_validation=evidence)
    monkeypatch.setattr(backend, "_runner_path", lambda: runner)

    with pytest.raises(PipelineError) as captured:
        backend.generate(store, rgba)
    assert captured.value.code == ErrorCode.OUTPUT_INVALID


def test_triposr_backend_propagates_worker_failure_and_timeout(
    tmp_path, monkeypatch, triposr_environment
) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    rgba = _rgba_artifact(store, tmp_path)
    repo, evidence = triposr_environment()
    backend = TripoSRBackend(Path(sys.executable), repo, frame_validation=evidence)
    failed = tmp_path / "failed.py"
    failed.write_text("raise RuntimeError('fixture failure')\n", encoding="utf-8")
    monkeypatch.setattr(backend, "_runner_path", lambda: failed)
    with pytest.raises(PipelineError) as captured:
        backend.generate(store, rgba)
    assert captured.value.code == ErrorCode.BACKEND_FAILED

    slow = tmp_path / "slow.py"
    slow.write_text("import time; time.sleep(1)\n", encoding="utf-8")
    backend = TripoSRBackend(
        Path(sys.executable), repo, timeout_seconds=0.01, frame_validation=evidence
    )
    monkeypatch.setattr(backend, "_runner_path", lambda: slow)
    with pytest.raises(PipelineError) as captured:
        backend.generate(store, rgba)
    assert captured.value.code == ErrorCode.BACKEND_TIMEOUT


def test_triposr_backend_requires_frame_validation_evidence(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    rgba = _rgba_artifact(store, tmp_path)
    backend = TripoSRBackend(Path(sys.executable), tmp_path)

    with pytest.raises(PipelineError, match="requires frame validation") as captured:
        backend.generate(store, rgba)

    assert captured.value.code == ErrorCode.BACKEND_UNAVAILABLE


def test_triposr_backend_rejects_stale_frame_validation(tmp_path, triposr_environment) -> None:
    repo, evidence = triposr_environment()
    (repo / "fixture.py").write_text("FRAME = '-Z'\n", encoding="utf-8")
    backend = TripoSRBackend(Path(sys.executable), repo, frame_validation=evidence)

    with pytest.raises(PipelineError, match="does not match") as captured:
        backend._frame_validation_identity()

    assert captured.value.code == ErrorCode.BACKEND_UNAVAILABLE


def test_triposr_backend_rejects_non_object_frame_validation(tmp_path, triposr_environment) -> None:
    repo, evidence = triposr_environment()
    evidence.write_text("[]", encoding="utf-8")
    backend = TripoSRBackend(Path(sys.executable), repo, frame_validation=evidence)

    with pytest.raises(PipelineError, match="expected a JSON object") as captured:
        backend._frame_validation_identity()

    assert captured.value.code == ErrorCode.BACKEND_UNAVAILABLE


def test_triposr_backend_rejects_non_utf8_frame_validation(tmp_path, triposr_environment) -> None:
    repo, evidence = triposr_environment()
    evidence.write_bytes(b"\xff")
    backend = TripoSRBackend(Path(sys.executable), repo, frame_validation=evidence)

    with pytest.raises(PipelineError, match="invalid TripoSR frame validation") as captured:
        backend._frame_validation_identity()

    assert captured.value.code == ErrorCode.BACKEND_UNAVAILABLE


def test_triposr_backend_rejects_environment_mismatch(
    tmp_path, monkeypatch, triposr_environment
) -> None:
    repo, evidence = triposr_environment()
    monkeypatch.setattr(
        "assets_generator.operators.backend_environment_identity", lambda python: {"fixture": False}
    )
    backend = TripoSRBackend(Path(sys.executable), repo, frame_validation=evidence)

    with pytest.raises(PipelineError, match="does not match") as captured:
        backend._frame_validation_identity()

    assert captured.value.code == ErrorCode.BACKEND_UNAVAILABLE


@pytest.mark.parametrize("field", ["fixture_digest", "validator_digest"])
def test_triposr_backend_rejects_frame_tool_mismatch(tmp_path, triposr_environment, field) -> None:
    repo, evidence = triposr_environment()
    content = json.loads(evidence.read_text(encoding="utf-8"))
    content[field] = "sha256:" + "0" * 64
    evidence.write_text(json.dumps(content), encoding="utf-8")
    backend = TripoSRBackend(Path(sys.executable), repo, frame_validation=evidence)

    with pytest.raises(PipelineError, match="does not match") as captured:
        backend._frame_validation_identity()

    assert captured.value.code == ErrorCode.BACKEND_UNAVAILABLE


def test_triposr_backend_rejects_repo_change_during_execution(
    tmp_path, monkeypatch, triposr_environment
) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    rgba = _rgba_artifact(store, tmp_path)
    repo, evidence = triposr_environment()
    fixture_runner = (Path(__file__).parent / "fixtures" / "triposr_path_runner.py").resolve()
    runner = tmp_path / "changing_repo_runner.py"
    runner.write_text(
        "import json, runpy, sys\n"
        "from pathlib import Path\n"
        "request = json.loads(Path(sys.argv[1]).read_text())\n"
        f"runpy.run_path({str(fixture_runner)!r})\n"
        "(Path(request['repo']) / 'fixture.py').write_text(\"FRAME = '-Z'\\n\")\n",
        encoding="utf-8",
    )
    backend = TripoSRBackend(Path(sys.executable), repo, frame_validation=evidence)
    monkeypatch.setattr(backend, "_runner_path", lambda: runner)

    with pytest.raises(PipelineError, match="changed during Backend execution") as captured:
        backend.generate(store, rgba)

    assert captured.value.code == ErrorCode.BACKEND_UNAVAILABLE


def test_segmentation_backend_publishes_binary_mask_and_reuses_cache(tmp_path, monkeypatch) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (12, 8), (20, 40, 60)).save(image_path)
    store = LocalArtifactStore(tmp_path / "store")
    image = store.persist_bytes(
        image_path.read_bytes(),
        kind="rgb_image",
        schema_name="raster_image",
        schema_version="1.0",
    )
    runner = (Path(__file__).parent / "fixtures" / "segmentation_path_runner.py").resolve()
    backend = BiRefNetSegmentationBackend(Path(sys.executable))
    monkeypatch.setattr(backend, "_runner_path", lambda: runner)

    first = backend.segment(store, image)
    second = backend.segment(store, image)

    assert not first.cache_hit
    assert second.cache_hit
    assert first.mask == second.mask
    with Image.open(store.blob_path(first.mask)) as mask:
        assert mask.size == (12, 8)
        assert set(np.unique(np.asarray(mask))) <= {0, 255}

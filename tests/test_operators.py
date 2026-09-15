from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.models import (
    SCHEMA_VERSION,
    ArtifactRef,
    BackendNativeFrame,
    ProvenanceRecord,
)
from assets_generator.operators import (
    OperatorExecutionError,
    Trellis2Backend,
    canonicalize_glb,
    validate_geometry,
)
from assets_generator.serialization import canonical_json_bytes


def _box_glb() -> bytes:
    scene = trimesh.Scene(trimesh.creation.box(extents=[2.0, 1.0, 4.0]))
    result = scene.export(file_type="glb")
    assert isinstance(result, bytes)
    return result


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

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 0.01)

    monkeypatch.setattr("assets_generator.operators.subprocess.run", timeout)
    with pytest.raises(OperatorExecutionError, match="timed out after 0.01 seconds"):
        backend.generate(store, rgba)

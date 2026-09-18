"""Explicit user-supplied rigid-body property assignment."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from ctypes import CDLL, c_char_p, c_int, get_errno
from dataclasses import dataclass
from errno import ENOSYS
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import trimesh

from .artifact_store import LocalArtifactStore
from .collision import _artifact
from .contracts import ContractError, validate_operator_inputs, validate_operator_outputs
from .errors import ErrorCode, classify_error
from .models import (
    SCHEMA_VERSION,
    ArtifactRef,
    AssetRelease,
    BuildRun,
    NodeAttempt,
    PhysicsInfo,
    PortValue,
    QualityCheck,
    QualityReport,
    StructuredValue,
)
from .operators import _load_scene, _scene_vertices
from .pipeline import load_default_operator_specs
from .runtime import utc_now
from .scene_workflow import _release_files
from .serialization import canonical_json_bytes, sha256_bytes, to_primitive
from .workflow import _materialize_release, _persist_build_run, _persist_provenance


@dataclass(frozen=True)
class RigidBodyResult:
    run_id: str
    properties: ArtifactRef
    asset_definition: ArtifactRef
    release_manifest: ArtifactRef
    quality_report: ArtifactRef
    output_directory: Path


def _finite_number(value: object, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"rigid-body {field} must be a finite number")
    number = float(value)
    if not np.isfinite(number):
        raise ContractError(f"rigid-body {field} must be a finite number")
    if positive and number <= 0:
        raise ContractError(f"rigid-body {field} must be positive")
    return 0.0 if number == 0 else number


def _properties(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractError("rigid-body properties must be readable UTF-8 JSON") from error
    required = {
        "schema_version",
        "source_release_id",
        "source_asset_id",
        "collision_artifact_id",
        "frame_id",
        "unit",
        "body_type",
        "mass_kg",
        "center_of_mass_m",
        "inertia_kg_m2",
        "static_friction",
        "dynamic_friction",
        "restitution",
        "source",
        "provided_by",
    }
    if not isinstance(raw, dict) or set(raw) != required or raw.get("schema_version") != "1.0":
        raise ContractError(f"rigid-body properties require exactly {sorted(required)}")
    for field in ("source_release_id", "source_asset_id", "collision_artifact_id", "frame_id"):
        if not isinstance(raw[field], str) or not raw[field]:
            raise ContractError(f"rigid-body {field} must be nonempty")
    if raw["unit"] != "meter":
        raise ContractError("rigid-body properties must use meter units")
    if raw["body_type"] != "dynamic":
        raise ContractError("rigid-body v1 only supports dynamic bodies")
    if raw["source"] != "user":
        raise ContractError("rigid-body source must be user")
    if not isinstance(raw["provided_by"], str) or not raw["provided_by"].strip():
        raise ContractError("rigid-body provided_by must be nonempty")
    raw["mass_kg"] = _finite_number(raw["mass_kg"], "mass_kg", positive=True)
    center = raw["center_of_mass_m"]
    if not isinstance(center, list) or len(center) != 3:
        raise ContractError("rigid-body center_of_mass_m must contain three coordinates")
    raw["center_of_mass_m"] = [
        _finite_number(value, f"center_of_mass_m[{index}]") for index, value in enumerate(center)
    ]
    tensor = raw["inertia_kg_m2"]
    if (
        not isinstance(tensor, list)
        or len(tensor) != 3
        or any(not isinstance(row, list) or len(row) != 3 for row in tensor)
    ):
        raise ContractError("rigid-body inertia_kg_m2 must be a 3x3 tensor")
    matrix = np.asarray(
        [
            [
                _finite_number(value, f"inertia_kg_m2[{row}][{column}]")
                for column, value in enumerate(values)
            ]
            for row, values in enumerate(tensor)
        ],
        dtype=np.float64,
    )
    tensor_scale = float(np.max(np.abs(matrix)))
    tolerance = max(tensor_scale * 1e-12, np.spacing(tensor_scale) * 16)
    if not np.allclose(matrix, matrix.T, rtol=0.0, atol=tolerance):
        raise ContractError("rigid-body inertia_kg_m2 must be symmetric")
    eigenvalues = np.linalg.eigvalsh(matrix)
    eigenvalue_scale = float(np.max(np.abs(eigenvalues)))
    eigenvalue_tolerance = max(eigenvalue_scale * 1e-12, np.spacing(eigenvalue_scale) * 16)
    if eigenvalues[0] <= eigenvalue_tolerance:
        raise ContractError("rigid-body inertia_kg_m2 must be positive definite")
    if eigenvalues[-1] > eigenvalues[0] + eigenvalues[1] + eigenvalue_tolerance:
        raise ContractError("rigid-body inertia principal moments violate triangle inequality")
    raw["inertia_kg_m2"] = matrix.tolist()
    for field in ("static_friction", "dynamic_friction"):
        raw[field] = _finite_number(raw[field], field)
        if raw[field] < 0:
            raise ContractError(f"rigid-body {field} must be nonnegative")
    raw["restitution"] = _finite_number(raw["restitution"], "restitution")
    if not 0 <= raw["restitution"] <= 1:
        raise ContractError("rigid-body restitution must be between 0 and 1")
    if raw["dynamic_friction"] > raw["static_friction"]:
        raise ContractError("rigid-body dynamic_friction cannot exceed static_friction")
    return raw


def _publish_staged_release(staging_path: Path, output_path: Path) -> None:
    libc = CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(ENOSYS, "atomic no-replace publication is unavailable")
    renameat2.argtypes = [c_int, c_char_p, c_int, c_char_p, c_int]
    renameat2.restype = c_int
    if (
        renameat2(
            -100,
            os.fsencode(staging_path),
            -100,
            os.fsencode(output_path),
            1,
        )
        != 0
    ):
        error_number = get_errno()
        raise OSError(error_number, os.strerror(error_number), output_path)


def _validated_collision_scene(store: LocalArtifactStore, collision: ArtifactRef) -> None:
    scene = _load_scene(store.blob_path(collision).read_bytes())
    meshes: list[trimesh.Trimesh] = []
    for node in sorted(scene.graph.nodes_geometry):
        transform, geometry_name = scene.graph[node]
        mesh = scene.geometry[geometry_name].copy()
        mesh.apply_transform(transform)
        meshes.append(mesh)
    if not meshes:
        raise ContractError("source collision mesh is empty")
    combined = trimesh.util.concatenate(meshes)
    if not isinstance(combined, trimesh.Trimesh):
        raise ContractError("source collision mesh is not triangular geometry")
    vertices = np.asarray(combined.vertices, dtype=np.float64)
    if not len(vertices) or not np.isfinite(vertices).all():
        raise ContractError("source collision mesh is empty or nonfinite")
    if not combined.is_watertight or not combined.is_convex or combined.volume <= 0:
        raise ContractError("source collision mesh must be watertight, convex, and positive-volume")


def _has_collision_export_provenance(
    store: LocalArtifactStore,
    source_files: dict[str, ArtifactRef],
    collision: ArtifactRef,
    collision_glb: ArtifactRef,
) -> bool:
    for reference in source_files.values():
        manifest = store.get_manifest(reference.artifact_id)
        if manifest.identity.kind != "provenance_record":
            continue
        record = store.read_structured(reference)
        derived = record.get("derived_from_artifact_ids")
        if (
            record.get("output_artifact_id") == collision_glb.artifact_id
            and isinstance(derived, list)
            and collision.artifact_id in derived
        ):
            return True
    return False


def apply_rigid_body(
    *, release: ArtifactRef, properties_path: Path, store_path: Path, output_path: Path
) -> RigidBodyResult:
    """Create a new immutable release with explicit dynamic rigid-body properties."""
    store = LocalArtifactStore(store_path)
    import_attempt = NodeAttempt(
        "import_rigid_body_properties",
        1,
        "rigid_body_properties_import@1",
        "core",
        "running",
        "executed",
        utc_now(),
        None,
        None,
    )
    run = BuildRun(
        f"run_{uuid.uuid4().hex}",
        "rigid_body_v1",
        "1",
        "running",
        {"release": release},
        [import_attempt],
        utc_now(),
        None,
    )
    _persist_build_run(store, run)
    try:
        raw = _properties(properties_path)
        properties = store.persist_structured(
            StructuredValue("rigid_body_properties", "RigidBodyProperties", SCHEMA_VERSION, raw)
        )
        import_spec = load_default_operator_specs()["rigid_body_properties_import@1"]
        validate_operator_outputs(import_spec, {"properties": properties}, store)
        import_attempt.status = "succeeded"
        import_attempt.finished_at = utc_now()
        import_attempt.outputs = {"properties": properties}
        run.inputs["properties"] = properties

        assignment = NodeAttempt(
            "assign_rigid_body",
            1,
            "rigid_body_assignment@1",
            "core",
            "running",
            "executed",
            utc_now(),
            None,
            None,
        )
        run.node_attempts.append(assignment)
        _persist_build_run(store, run)
        if output_path.exists():
            raise FileExistsError(output_path)
        if not store.verify_digest(release):
            raise ContractError("source release has an invalid digest")
        if store.get_manifest(release.artifact_id).identity.kind != "asset_release":
            raise ContractError("source artifact is not an AssetRelease")
        release_raw = store.read_structured(release)
        source_files = _release_files(store, release)
        added_paths = {
            "evidence/rigid-body-properties.json",
            "qa/rigid-body-report.json",
            "provenance/rigid-body-properties.json",
            "provenance/rigid-body-quality.json",
            "provenance/rigid-body-asset.json",
        }
        conflicts = added_paths & source_files.keys()
        for added in added_paths:
            path = PurePosixPath(added)
            conflicts.update(
                name
                for name in source_files
                if PurePosixPath(name) in path.parents or path in PurePosixPath(name).parents
            )
        if conflicts:
            raise ContractError(
                f"source release conflicts with rigid-body output paths: {sorted(conflicts)}"
            )
        source_asset = _artifact(release_raw.get("asset_definition"), "asset_definition")
        if not store.verify_digest(source_asset):
            raise ContractError("source AssetDefinition has an invalid digest")
        asset = store.read_structured(source_asset)
        spatial = asset.get("spatial")
        geometry = asset.get("geometry")
        if not isinstance(spatial, dict) or not isinstance(geometry, dict):
            raise ContractError("source AssetDefinition spatial/geometry is invalid")
        if spatial.get("unit") != "meter" or spatial.get("scale_status") != "metric":
            raise ContractError("rigid-body assignment requires a metric source asset")
        if asset.get("physics") is not None:
            raise ContractError("rigid-body assignment requires physics to be null")
        visuals = geometry.get("visual_meshes")
        collisions = geometry.get("collision_meshes")
        if not isinstance(visuals, list) or len(visuals) != 1:
            raise ContractError("rigid-body assignment requires exactly one visual mesh")
        if not isinstance(collisions, list) or len(collisions) != 1:
            raise ContractError("rigid-body assignment requires exactly one collision mesh")
        collision = _artifact(collisions[0], "collision_meshes[0]")
        if (
            raw["source_release_id"] != release.artifact_id
            or raw["source_asset_id"] != source_asset.artifact_id
            or raw["collision_artifact_id"] != collision.artifact_id
            or raw["frame_id"] != spatial.get("canonical_frame_id")
        ):
            raise ContractError("rigid-body properties do not match the selected release and frame")
        if not store.verify_digest(collision):
            raise ContractError("source collision mesh has an invalid digest")
        collision_manifest = store.get_manifest(collision.artifact_id)
        metadata = collision_manifest.identity.identity_metadata
        if collision_manifest.identity.kind != "collision_mesh" or any(
            (
                metadata.get("frame_id") != spatial.get("canonical_frame_id"),
                metadata.get("unit") != "meter",
                metadata.get("up_axis") != "+Z",
                metadata.get("forward_axis") != "+X",
            )
        ):
            raise ContractError("source collision mesh does not match canonical metric space")
        _validated_collision_scene(store, collision)
        collision_glb = source_files.get("geometry/collision.glb")
        if collision_glb is None:
            raise ContractError("source release must contain geometry/collision.glb")
        collision_glb_manifest = store.get_manifest(collision_glb.artifact_id)
        collision_glb_metadata = collision_glb_manifest.identity.identity_metadata
        if collision_glb_manifest.identity.kind != "gltf_asset" or any(
            (
                collision_glb_metadata.get("frame_id") != "gltf_export",
                collision_glb_metadata.get("unit") != "meter",
                collision_glb_metadata.get("up_axis") != "+Y",
                collision_glb_metadata.get("forward_axis") != "+Z",
            )
        ):
            raise ContractError("release collision GLB does not match metric glTF space")
        exported_scene = _load_scene(store.blob_path(collision_glb).read_bytes())
        exported_vertices = _scene_vertices(exported_scene)
        if not len(exported_vertices) or not np.isfinite(exported_vertices).all():
            raise ContractError("release collision GLB is empty or nonfinite")
        if not _has_collision_export_provenance(store, source_files, collision, collision_glb):
            raise ContractError(
                "release collision GLB lacks provenance from the selected collision mesh"
            )
        physics = PhysicsInfo(
            schema_version="1.0",
            body_type="dynamic",
            mass_kg=raw["mass_kg"],
            center_of_mass_m=raw["center_of_mass_m"],
            inertia_kg_m2=raw["inertia_kg_m2"],
            static_friction=raw["static_friction"],
            dynamic_friction=raw["dynamic_friction"],
            restitution=raw["restitution"],
            collision_mesh=collision,
            frame_id=raw["frame_id"],
            unit="meter",
            source="user",
            provided_by=raw["provided_by"],
            evidence=properties,
        )
        checks = [
            QualityCheck("rigid_body_mass", True, physics.mass_kg, "rigid-body-v1", "pass"),
            QualityCheck("rigid_body_inertia", True, "positive-definite", "rigid-body-v1", "pass"),
            QualityCheck(
                "rigid_body_collision_binding",
                True,
                collision.artifact_id,
                "rigid-body-v1",
                "pass",
                evidence_artifacts=[collision, collision_glb, properties],
            ),
            QualityCheck("rigid_body_spatial_contract", True, "meter", "rigid-body-v1", "pass"),
        ]
        quality = store.persist_structured(
            StructuredValue(
                "quality_report",
                "QualityReport",
                SCHEMA_VERSION,
                to_primitive(QualityReport("rigid-body-v1", checks, "pass")),
            )
        )
        updated = dict(asset)
        updated["physics"] = to_primitive(physics)
        quality_ids = asset.get("quality_report_ids")
        if not isinstance(quality_ids, list) or not all(
            isinstance(item, str) for item in quality_ids
        ):
            raise ContractError("source quality_report_ids is invalid")
        updated["quality_report_ids"] = [*quality_ids, quality.artifact_id]
        identity = sha256_bytes(
            canonical_json_bytes(
                [source_asset.artifact_id, properties.artifact_id, quality.artifact_id]
            )
        )
        updated["asset_id"] = f"asset_{identity.split(':', 1)[1]}"
        updated["asset_version"] = "1.0"
        asset_ref = store.persist_structured(
            StructuredValue("asset_definition", "AssetDefinition", SCHEMA_VERSION, updated)
        )
        assignment_spec = load_default_operator_specs()["rigid_body_assignment@1"]
        assignment_inputs: dict[str, PortValue | list[PortValue]] = {
            "source_asset": source_asset,
            "properties": properties,
            "collision": collision,
        }
        assignment_outputs: dict[str, PortValue | list[PortValue]] = {
            "asset": asset_ref,
            "report": quality,
        }
        validate_operator_inputs(assignment_spec, assignment_inputs, store)
        validate_operator_outputs(assignment_spec, assignment_outputs, store)
        properties_provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id=import_attempt.node_id,
            port_name="properties",
            artifact=properties,
            derived_from=[],
            operator="rigid_body_properties_import",
            backend="core",
            backend_version="1",
            parameters={"source_path": properties_path.name},
            seed=None,
            source="user",
        )
        quality_provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id=assignment.node_id,
            port_name="report",
            artifact=quality,
            derived_from=[source_asset, properties, collision],
            operator="rigid_body_assignment",
            backend="core",
            backend_version="explicit-si-v1",
            parameters={"profile": "rigid-body-v1"},
            seed=None,
            source="derived",
        )
        asset_provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id=assignment.node_id,
            port_name="asset",
            artifact=asset_ref,
            derived_from=[source_asset, properties, collision, quality],
            operator="rigid_body_assignment",
            backend="core",
            backend_version="explicit-si-v1",
            parameters={"body_type": "dynamic"},
            seed=None,
            source="derived",
        )
        assignment.status = "succeeded"
        assignment.finished_at = utc_now()
        assignment.outputs = assignment_outputs

        files = dict(source_files)
        files.update(
            {
                "evidence/rigid-body-properties.json": properties,
                "qa/rigid-body-report.json": quality,
                "provenance/rigid-body-properties.json": properties_provenance,
                "provenance/rigid-body-quality.json": quality_provenance,
                "provenance/rigid-body-asset.json": asset_provenance,
            }
        )
        export_profile = release_raw.get("export_profile")
        if not isinstance(export_profile, str) or not export_profile:
            raise ContractError("source AssetRelease export_profile is invalid")
        result_release = AssetRelease(asset_ref, files, export_profile)
        release_attempt = NodeAttempt(
            "derive_rigid_body_release",
            1,
            "rigid_body_release@1",
            "core",
            "running",
            "executed",
            utc_now(),
            None,
            None,
        )
        run.node_attempts.append(release_attempt)
        release_ref = store.persist_structured(
            StructuredValue(
                "asset_release", "AssetRelease", SCHEMA_VERSION, to_primitive(result_release)
            )
        )
        _persist_provenance(
            store,
            run_id=run.run_id,
            node_id=release_attempt.node_id,
            port_name="release",
            artifact=release_ref,
            derived_from=[release, asset_ref, properties, quality],
            operator="rigid_body_release",
            backend="core",
            backend_version="1",
            parameters={"export_profile": result_release.export_profile},
            seed=None,
            source="derived",
        )
        release_spec = load_default_operator_specs()["rigid_body_release@1"]
        release_inputs: dict[str, PortValue | list[PortValue]] = {
            "source_release": release,
            "asset": asset_ref,
            "properties": properties,
            "report": quality,
        }
        validate_operator_inputs(release_spec, release_inputs, store)
        validate_operator_outputs(release_spec, {"release": release_ref}, store)
        release_attempt.status = "succeeded"
        release_attempt.finished_at = utc_now()
        release_attempt.outputs = {"release": release_ref}

        materialize = NodeAttempt(
            "materialize_release",
            1,
            "release_materialization@1",
            "core",
            "running",
            "executed",
            utc_now(),
            None,
            None,
        )
        run.node_attempts.append(materialize)
        running_run_ref = _persist_build_run(store, run)
        absolute_output = output_path.expanduser().absolute()
        staging_path = absolute_output.parent / f".{absolute_output.name}.{uuid.uuid4().hex}.staged"
        _materialize_release(store, staging_path, release_ref, result_release, running_run_ref)
        materialize.status = "succeeded"
        materialize.finished_at = utc_now()
        run.status = "succeeded"
        run.finished_at = materialize.finished_at
        final_run_ref = _persist_build_run(store, run)
        target_run_path = staging_path / "run.json"
        temporary_run_path = target_run_path.with_name(f".run.{uuid.uuid4().hex}.tmp")
        temporary_run_path.write_bytes(store.blob_path(final_run_ref).read_bytes())
        os.replace(temporary_run_path, target_run_path)
        _publish_staged_release(staging_path, absolute_output)
        return RigidBodyResult(
            run.run_id,
            properties,
            asset_ref,
            release_ref,
            quality,
            absolute_output,
        )
    except Exception as error:
        if "staging_path" in locals():
            shutil.rmtree(staging_path, ignore_errors=True)
        active = run.node_attempts[-1]
        active.status = "failed"
        active.finished_at = utc_now()
        active.error_code = (
            ErrorCode.RELEASE_FAILED.value
            if active.node_id == "materialize_release"
            else classify_error(error).value
        )
        run.status = "failed"
        run.finished_at = active.finished_at
        _persist_build_run(store, run)
        raise

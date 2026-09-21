"""Deterministic, explicitly requested collision proxy generation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import numpy as np
import scipy  # type: ignore[import-untyped]
import trimesh

from .artifact_store import ArtifactStoreError, LocalArtifactStore
from .contracts import ContractError, validate_operator_inputs, validate_operator_outputs
from .errors import ErrorCode, classify_error
from .mesh_io import load_scene as _load_scene
from .mesh_io import scene_vertices as _scene_vertices
from .models import (
    SCHEMA_VERSION,
    ArtifactRef,
    AssetRelease,
    BuildRun,
    NodeAttempt,
    QualityCheck,
    QualityReport,
    StructuredValue,
)
from .pipeline import load_default_operator_specs
from .provenance import persist_build_run as _persist_build_run
from .provenance import persist_provenance as _persist_provenance
from .release_io import materialize_release as _materialize_release
from .runtime import utc_now
from .serialization import canonical_json_bytes, sha256_bytes, to_primitive


@dataclass(frozen=True)
class CollisionBuildResult:
    run_id: str
    asset_definition: ArtifactRef
    release_manifest: ArtifactRef
    collision: ArtifactRef
    collision_glb: ArtifactRef
    quality_report: ArtifactRef
    output_directory: Path


def _artifact(value: object, field: str) -> ArtifactRef:
    if not isinstance(value, dict) or set(value) != {"artifact_id"}:
        raise ContractError(f"{field} must be an ArtifactRef")
    artifact_id = value.get("artifact_id")
    if not isinstance(artifact_id, str):
        raise ContractError(f"{field} must contain an artifact ID")
    return ArtifactRef(artifact_id)


def _release_files(store: LocalArtifactStore, raw: dict[str, Any]) -> dict[str, ArtifactRef]:
    from .release_io import release_files as checked_release_files

    temporary = store.persist_structured(
        StructuredValue("asset_release", "AssetRelease", SCHEMA_VERSION, raw)
    )
    return checked_release_files(store, temporary)


def _canonical_vertices(store: LocalArtifactStore, mesh: ArtifactRef) -> np.ndarray[Any, Any]:
    scene = _load_scene(store.blob_path(mesh).read_bytes())
    vertices: list[np.ndarray[Any, Any]] = []
    for node in sorted(scene.graph.nodes_geometry):
        transform, geometry_name = scene.graph[node]
        geometry = scene.geometry[geometry_name]
        if not len(geometry.vertices) or not len(geometry.faces):
            raise ContractError("visual mesh geometry must be nonempty")
        points = trimesh.transform_points(np.asarray(geometry.vertices), transform)
        vertices.append(np.asarray(points, dtype=np.float64))
    if not vertices:
        raise ContractError("visual mesh has no geometry")
    combined = np.vstack(vertices)
    if not np.isfinite(combined).all():
        raise ContractError("visual mesh contains nonfinite vertices")
    if np.linalg.matrix_rank(combined - combined[0], tol=1e-12) < 3:
        raise ContractError("visual mesh is degenerate and has no three-dimensional hull")
    return combined


def _stable_hull(vertices: np.ndarray[Any, Any]) -> trimesh.Trimesh:
    try:
        generated = trimesh.convex.convex_hull(vertices, qhull_options="QbB Pp Qt")
    except Exception as error:
        raise ContractError(f"convex hull generation failed: {error}") from error
    hull_vertices = np.asarray(generated.vertices, dtype=np.float64)
    hull_faces = np.asarray(generated.faces, dtype=np.int64)
    if not len(hull_vertices) or not len(hull_faces) or not np.isfinite(hull_vertices).all():
        raise ContractError("convex hull output is empty or nonfinite")

    vertex_order = np.lexsort((hull_vertices[:, 2], hull_vertices[:, 1], hull_vertices[:, 0]))
    inverse = np.empty(len(vertex_order), dtype=np.int64)
    inverse[vertex_order] = np.arange(len(vertex_order))
    hull_faces = inverse[hull_faces]
    normalized_faces = []
    for face in hull_faces:
        start = int(np.argmin(face))
        normalized_faces.append(np.roll(face, -start))
    stable_faces = np.asarray(normalized_faces, dtype=np.int64)
    face_order = np.lexsort((stable_faces[:, 2], stable_faces[:, 1], stable_faces[:, 0]))
    stable_faces = stable_faces[face_order]
    result = trimesh.Trimesh(
        vertices=hull_vertices[vertex_order], faces=stable_faces, process=False
    )
    if not result.is_convex or result.volume <= 0:
        raise ContractError("convex hull output is not a valid volume")
    return result


def _glb(mesh: trimesh.Trimesh, transform: np.ndarray[Any, Any] | None = None) -> bytes:
    scene = trimesh.Scene(mesh.copy())
    if transform is not None:
        scene.apply_transform(transform)  # type: ignore[no-untyped-call]
    data = scene.export(file_type="glb")  # type: ignore[no-untyped-call]
    if not isinstance(data, bytes):
        raise ContractError("trimesh did not produce binary GLB output")
    return data


def _quality_report(
    store: LocalArtifactStore,
    collision: ArtifactRef,
    collision_glb: ArtifactRef,
    source_release: ArtifactRef,
    source_metadata: dict[str, Any],
    provenance: ArtifactRef,
) -> QualityReport:
    checks: list[QualityCheck] = []
    try:
        scene = _load_scene(store.blob_path(collision).read_bytes())
        exported_scene = _load_scene(store.blob_path(collision_glb).read_bytes())
        vertices = _scene_vertices(scene)
        exported_vertices = _scene_vertices(exported_scene)
        faces = sum(len(geometry.faces) for geometry in scene.geometry.values())
        meshes = list(scene.geometry.values())
        loadable = bool(
            len(vertices)
            and faces
            and np.isfinite(vertices).all()
            and len(exported_vertices)
            and np.isfinite(exported_vertices).all()
            and meshes
            and all(mesh.is_convex and mesh.is_watertight and mesh.volume > 0 for mesh in meshes)
        )
    except Exception:
        loadable, faces = False, 0
    checks.append(
        QualityCheck(
            "collision_loadable",
            True,
            faces,
            "collision-convex-hull-v1",
            "pass" if loadable else "fail",
            evidence_artifacts=[collision, collision_glb, provenance],
        )
    )
    metadata = store.get_manifest(collision.artifact_id).identity.identity_metadata
    spatial = all(
        metadata.get(key) == source_metadata.get(key)
        for key in ("frame_id", "unit", "up_axis", "forward_axis")
    )
    checks.append(
        QualityCheck(
            "collision_spatial_contract",
            True,
            "preserved" if spatial else "mismatch",
            "collision-convex-hull-v1",
            "pass" if spatial else "fail",
            evidence_artifacts=[collision],
        )
    )
    record = store.read_structured(provenance)
    provenance_ok = (
        store.verify_digest(provenance)
        and record.get("output_artifact_id") == collision.artifact_id
        and record.get("operator") == "collision_generation"
    )
    checks.append(
        QualityCheck(
            "mandatory_collision_provenance",
            True,
            provenance.artifact_id,
            "collision-convex-hull-v1",
            "pass" if provenance_ok else "fail",
            evidence_artifacts=[provenance],
        )
    )
    checks.append(
        QualityCheck(
            "source_release_verified",
            True,
            source_release.artifact_id,
            "collision-convex-hull-v1",
            "pass" if store.verify_digest(source_release) else "fail",
            evidence_artifacts=[source_release],
        )
    )
    overall: Literal["pass", "fail"] = (
        "pass" if all(check.status == "pass" for check in checks) else "fail"
    )
    return QualityReport("collision-convex-hull-v1", checks, overall)


def build_collision_asset(
    *,
    release: ArtifactRef,
    store_path: Path,
    output_path: Path,
    method: Literal["convex-hull"] = "convex-hull",
) -> CollisionBuildResult:
    """Create a new release with a deterministic convex collision proxy."""
    if method != "convex-hull":
        raise ContractError(f"unsupported collision method: {method}")
    store = LocalArtifactStore(store_path)
    run = BuildRun(
        f"run_{uuid.uuid4().hex}",
        "collision_asset_v1",
        "1",
        "running",
        {"release": release},
        [
            NodeAttempt(
                "generate_collision",
                1,
                "collision_generation@1",
                "core",
                "running",
                "executed",
                utc_now(),
                None,
                None,
            )
        ],
        utc_now(),
        None,
    )
    _persist_build_run(store, run)
    try:
        if output_path.exists():
            raise FileExistsError(output_path)
        if not store.verify_digest(release):
            raise ContractError("source release has an invalid digest")
        release_manifest = store.get_manifest(release.artifact_id)
        if release_manifest.identity.kind != "asset_release":
            raise ContractError("source artifact is not an AssetRelease")
        source_release = store.read_structured(release)
        source_files = _release_files(store, source_release)
        added_paths = {
            "geometry/collision.glb",
            "qa/collision-quality-report.json",
            "provenance/collision.json",
            "provenance/collision-export.json",
            "provenance/collision-quality.json",
            "provenance/collision-asset.json",
        }
        conflicts = added_paths & source_files.keys()
        for added in added_paths:
            added_path = PurePosixPath(added)
            conflicts.update(
                name
                for name in source_files
                if PurePosixPath(name) in added_path.parents
                or added_path in PurePosixPath(name).parents
            )
        if conflicts:
            raise ContractError(
                f"source release already contains collision output paths: {sorted(conflicts)}"
            )
        source_asset = _artifact(source_release.get("asset_definition"), "asset_definition")
        if not store.verify_digest(source_asset):
            raise ContractError("source AssetDefinition has an invalid digest")
        asset_manifest = store.get_manifest(source_asset.artifact_id)
        if asset_manifest.identity.kind != "asset_definition":
            raise ContractError("release asset_definition has the wrong kind")
        asset = store.read_structured(source_asset)
        geometry = asset.get("geometry")
        if not isinstance(geometry, dict):
            raise ContractError("AssetDefinition geometry is invalid")
        visuals = geometry.get("visual_meshes")
        collisions = geometry.get("collision_meshes")
        if not isinstance(visuals, list) or len(visuals) != 1:
            raise ContractError("collision v1 requires exactly one canonical visual mesh")
        if not isinstance(collisions, list) or collisions:
            raise ContractError("source AssetDefinition must not already contain collision meshes")
        visual = _artifact(visuals[0], "visual_meshes[0]")
        if not store.verify_digest(visual):
            raise ContractError("canonical visual mesh has an invalid digest")
        visual_manifest = store.get_manifest(visual.artifact_id)
        if visual_manifest.identity.kind != "triangle_mesh":
            raise ContractError("canonical visual mesh must have triangle_mesh kind")
        metadata = visual_manifest.identity.identity_metadata
        required_spatial = ("frame_id", "unit", "up_axis", "forward_axis")
        if any(not metadata.get(key) for key in required_spatial):
            raise ContractError("canonical visual mesh has incomplete spatial metadata")
        spatial = asset.get("spatial")
        if not isinstance(spatial, dict):
            raise ContractError("AssetDefinition spatial is invalid")
        if (
            metadata["frame_id"] != spatial.get("canonical_frame_id")
            or metadata["unit"] != spatial.get("unit")
            or metadata["up_axis"] != "+Z"
            or metadata["forward_axis"] != "+X"
        ):
            raise ContractError(
                "canonical visual mesh must match AssetDefinition spatial and +Z/+X axes"
            )

        spec = load_default_operator_specs()["collision_generation@1"]
        validate_operator_inputs(spec, {"mesh": visual}, store)
        hull = _stable_hull(_canonical_vertices(store, visual))
        parameters = {
            "algorithm": "convex_hull_v1",
            "qhull_options": "QbB Pp Qt",
            "numpy_version": np.__version__,
            "scipy_version": scipy.__version__,
            "trimesh_version": trimesh.__version__,
        }
        collision = store.persist_bytes(
            _glb(hull),
            kind="collision_mesh",
            schema_name="glTF",
            schema_version="2.0",
            identity_metadata={
                **{key: metadata[key] for key in required_spatial},
                "media_type": "model/gltf-binary",
                "collision_method": "convex_hull_v1",
            },
        )
        validate_operator_outputs(spec, {"collision": collision}, store)
        generation_attempt = run.node_attempts[-1]
        generation_attempt.status = "succeeded"
        generation_attempt.finished_at = utc_now()
        generation_attempt.outputs = {"collision": collision}
        export_attempt = NodeAttempt(
            "export_collision_release",
            1,
            "collision_export@1",
            "core",
            "running",
            "executed",
            utc_now(),
            None,
            None,
        )
        run.node_attempts.append(export_attempt)
        canonical_to_gltf = np.array(
            [
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )
        collision_glb = store.persist_bytes(
            _glb(hull, canonical_to_gltf),
            kind="gltf_asset",
            schema_name="glTF",
            schema_version="2.0",
            identity_metadata={
                "media_type": "model/gltf-binary",
                "frame_id": "gltf_export",
                "unit": metadata["unit"],
                "up_axis": "+Y",
                "forward_axis": "+Z",
                "export_profile": "gltf2-collision-v1",
                "collision_method": "convex_hull_v1",
            },
        )
        collision_provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id="generate_collision",
            port_name="collision",
            artifact=collision,
            derived_from=[release, source_asset, visual],
            operator="collision_generation",
            backend="core",
            backend_version="convex-hull-v1",
            parameters=parameters,
            seed=None,
            source="derived",
        )
        export_spec = load_default_operator_specs()["collision_export@1"]
        validate_operator_inputs(export_spec, {"collision": collision}, store)
        validate_operator_outputs(export_spec, {"glb": collision_glb}, store)
        export_attempt.status = "succeeded"
        export_attempt.finished_at = utc_now()
        export_attempt.outputs = {"glb": collision_glb}
        validation_attempt = NodeAttempt(
            "validate_collision",
            1,
            "collision_validation@1",
            "core",
            "running",
            "executed",
            utc_now(),
            None,
            None,
        )
        run.node_attempts.append(validation_attempt)
        export_provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id="export_collision_release",
            port_name="glb",
            artifact=collision_glb,
            derived_from=[collision],
            operator="collision_export",
            backend="core",
            backend_version="gltf2-collision-v1",
            parameters={"canonical_to_gltf": canonical_to_gltf.tolist()},
            seed=None,
            source="derived",
        )
        report = _quality_report(
            store, collision, collision_glb, release, metadata, collision_provenance
        )
        if report.overall_status != "pass":
            raise ContractError("generated collision proxy failed collision QA")
        validation_spec = load_default_operator_specs()["collision_validation@1"]
        validate_operator_inputs(
            validation_spec,
            {"collision": collision, "provenance": collision_provenance},
            store,
        )
        quality = store.persist_structured(
            StructuredValue("quality_report", "QualityReport", SCHEMA_VERSION, to_primitive(report))
        )
        validate_operator_outputs(validation_spec, {"report": quality}, store)
        quality_provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id="validate_collision",
            port_name="report",
            artifact=quality,
            derived_from=[collision, collision_provenance],
            operator="collision_validation",
            backend="core",
            backend_version="collision-convex-hull-v1",
            parameters={"profile": "collision-convex-hull-v1"},
            seed=None,
            source="derived",
        )
        validation_attempt.status = "succeeded"
        validation_attempt.finished_at = utc_now()
        validation_attempt.outputs = {"report": quality}
        derivation_attempt = NodeAttempt(
            "derive_collision_asset",
            1,
            "collision_asset_derivation@1",
            "core",
            "running",
            "executed",
            utc_now(),
            None,
            None,
        )
        run.node_attempts.append(derivation_attempt)
        updated_asset = dict(asset)
        updated_geometry = dict(geometry)
        updated_geometry["collision_meshes"] = [to_primitive(collision)]
        updated_asset["geometry"] = updated_geometry
        version = asset.get("asset_version")
        if not isinstance(version, str) or not version:
            raise ContractError("AssetDefinition asset_version is invalid")
        derived_identity = sha256_bytes(
            canonical_json_bytes(
                ["collision-asset-v1", source_asset.artifact_id, "convex_hull_v1", parameters]
                + [quality.artifact_id]
            )
        ).split(":", 1)[1]
        updated_asset["asset_id"] = f"asset_{derived_identity}"
        updated_asset["asset_version"] = "1.0"
        quality_ids = asset.get("quality_report_ids")
        if not isinstance(quality_ids, list) or not all(
            isinstance(item, str) for item in quality_ids
        ):
            raise ContractError("AssetDefinition quality_report_ids is invalid")
        updated_asset["quality_report_ids"] = [*quality_ids, quality.artifact_id]
        asset_ref = store.persist_structured(
            StructuredValue("asset_definition", "AssetDefinition", SCHEMA_VERSION, updated_asset)
        )
        derivation_spec = load_default_operator_specs()["collision_asset_derivation@1"]
        validate_operator_inputs(
            derivation_spec,
            {"source_asset": source_asset, "collision": collision, "quality": quality},
            store,
        )
        validate_operator_outputs(derivation_spec, {"asset": asset_ref}, store)
        asset_provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id="derive_collision_asset",
            port_name="asset",
            artifact=asset_ref,
            derived_from=[source_asset, collision, quality],
            operator="collision_asset_derivation",
            backend="core",
            backend_version="collision-derivation-v1",
            parameters={
                "source_asset_id": asset.get("asset_id"),
                "source_asset_version": version,
                "collision_method": "convex_hull_v1",
            },
            seed=None,
            source="mixed",
        )
        derivation_attempt.status = "succeeded"
        derivation_attempt.finished_at = utc_now()
        derivation_attempt.outputs = {"asset": asset_ref}
        files = {
            **source_files,
            "geometry/collision.glb": collision_glb,
            "qa/collision-quality-report.json": quality,
            "provenance/collision.json": collision_provenance,
            "provenance/collision-export.json": export_provenance,
            "provenance/collision-quality.json": quality_provenance,
            "provenance/collision-asset.json": asset_provenance,
        }
        export_profile = source_release.get("export_profile")
        if not isinstance(export_profile, str) or not export_profile:
            raise ContractError("source AssetRelease export_profile is invalid")
        release_attempt = NodeAttempt(
            "derive_collision_release",
            1,
            "collision_release@1",
            "core",
            "running",
            "executed",
            utc_now(),
            None,
            None,
        )
        run.node_attempts.append(release_attempt)
        result_release = AssetRelease(
            asset_ref,
            files,
            export_profile,
        )
        result_release_ref = store.persist_structured(
            StructuredValue(
                "asset_release", "AssetRelease", SCHEMA_VERSION, to_primitive(result_release)
            )
        )
        _persist_provenance(
            store,
            run_id=run.run_id,
            node_id="derive_collision_release",
            port_name="release",
            artifact=result_release_ref,
            derived_from=[release, asset_ref, collision_glb, quality],
            operator="collision_release",
            backend="core",
            backend_version="gltf2-collision-v1",
            parameters={"export_profile": export_profile},
            seed=None,
            source="derived",
        )
        release_spec = load_default_operator_specs()["collision_release@1"]
        validate_operator_inputs(
            release_spec,
            {
                "source_release": release,
                "asset": asset_ref,
                "glb": collision_glb,
                "quality": quality,
            },
            store,
        )
        validate_operator_outputs(release_spec, {"release": result_release_ref}, store)
        release_attempt.status = "succeeded"
        release_attempt.finished_at = utc_now()
        release_attempt.outputs = {"release": result_release_ref}
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
        materialize.status = "succeeded"
        materialize.finished_at = utc_now()
        run.status = "succeeded"
        run.finished_at = materialize.finished_at
        run_ref = _persist_build_run(store, run)
        _materialize_release(store, output_path, result_release_ref, result_release, run_ref)
        return CollisionBuildResult(
            run.run_id,
            asset_ref,
            result_release_ref,
            collision,
            collision_glb,
            quality,
            output_path.expanduser().absolute(),
        )
    except Exception as error:
        attempt = run.node_attempts[-1]
        attempt.status = "failed"
        attempt.finished_at = utc_now()
        attempt.error_code = (
            ErrorCode.RELEASE_FAILED.value
            if attempt.node_id == "materialize_release"
            else classify_error(error).value
        )
        run.status = "failed"
        run.finished_at = attempt.finished_at
        try:
            _persist_build_run(store, run)
        except (ArtifactStoreError, OSError, ValueError):
            pass
        raise

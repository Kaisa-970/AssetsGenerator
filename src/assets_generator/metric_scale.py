"""Explicit point-distance metric scale calibration."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np

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
class MetricScaleResult:
    run_id: str
    measurement: ArtifactRef
    asset_definition: ArtifactRef
    release_manifest: ArtifactRef
    visual_mesh: ArtifactRef
    collision_mesh: ArtifactRef | None
    quality_report: ArtifactRef
    output_directory: Path


def _measurement(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractError("metric measurement must be readable UTF-8 JSON") from error
    required = {
        "schema_version",
        "source_asset_id",
        "visual_artifact_id",
        "frame_id",
        "unit",
        "point_a",
        "point_b",
        "distance_meters",
        "source",
    }
    if not isinstance(raw, dict) or set(raw) != required or raw.get("schema_version") != "1.0":
        raise ContractError(f"metric measurement requires exactly {sorted(required)}")
    for name in ("point_a", "point_b"):
        value = raw[name]
        if not isinstance(value, list) or len(value) != 3:
            raise ContractError(f"metric measurement {name} must contain three coordinates")
        try:
            point = np.asarray(value, dtype=np.float64)
        except (TypeError, ValueError) as error:
            raise ContractError(f"metric measurement {name} must be finite") from error
        if not np.isfinite(point).all():
            raise ContractError(f"metric measurement {name} must be finite")
        raw[name] = point.tolist()
    distance = raw["distance_meters"]
    if not isinstance(distance, (int, float)) or not np.isfinite(distance) or distance <= 0:
        raise ContractError("metric measurement distance_meters must be positive and finite")
    if not isinstance(raw["source"], str) or not raw["source"].strip():
        raise ContractError("metric measurement source must be nonempty")
    for name in ("source_asset_id", "visual_artifact_id", "frame_id"):
        if not isinstance(raw[name], str) or not raw[name]:
            raise ContractError(f"metric measurement {name} must be nonempty")
    if raw["unit"] != "relative_unit":
        raise ContractError("metric measurement points must use relative_unit")
    canonical_distance = float(
        np.linalg.norm(np.asarray(raw["point_b"]) - np.asarray(raw["point_a"]))
    )
    if canonical_distance <= 1e-12:
        raise ContractError("metric measurement points must be distinct")
    raw["distance_meters"] = float(distance)
    return raw


def _scaled_glb(
    store: LocalArtifactStore, source: ArtifactRef, scale: float
) -> tuple[bytes, np.ndarray[Any, Any]]:
    scene = _load_scene(store.blob_path(source).read_bytes())
    scene.apply_transform(np.diag([scale, scale, scale, 1.0]))  # type: ignore[no-untyped-call]
    vertices = _scene_vertices(scene)
    if not len(vertices) or not np.isfinite(vertices).all():
        raise ContractError("scaled mesh is empty or nonfinite")
    data = scene.export(file_type="glb")  # type: ignore[no-untyped-call]
    if not isinstance(data, bytes):
        raise ContractError("metric calibration did not produce GLB")
    return data, vertices


def _export_glb(store: LocalArtifactStore, mesh: ArtifactRef) -> bytes:
    scene = _load_scene(store.blob_path(mesh).read_bytes())
    conversion = np.array([[0, 1, 0, 0], [0, 0, 1, 0], [1, 0, 0, 0], [0, 0, 0, 1]], dtype=float)
    scene.apply_transform(conversion)  # type: ignore[no-untyped-call]
    data = scene.export(file_type="glb")  # type: ignore[no-untyped-call]
    if not isinstance(data, bytes):
        raise ContractError("metric export did not produce GLB")
    return data


def _publish_staged_release(staging_path: Path, output_path: Path) -> None:
    os.replace(staging_path, output_path)


def calibrate_metric_scale(
    *, release: ArtifactRef, measurement_path: Path, store_path: Path, output_path: Path
) -> MetricScaleResult:
    store = LocalArtifactStore(store_path)
    import_attempt = NodeAttempt(
        "import_metric_measurement",
        1,
        "metric_scale_measurement_import@1",
        "core",
        "running",
        "executed",
        utc_now(),
        None,
        None,
    )
    run = BuildRun(
        f"run_{uuid.uuid4().hex}",
        "metric_scale_v1",
        "1",
        "running",
        {"release": release},
        [import_attempt],
        utc_now(),
        None,
    )
    _persist_build_run(store, run)
    try:
        measurement_raw = _measurement(measurement_path)
        measurement = store.persist_structured(
            StructuredValue(
                "metric_scale_measurement",
                "MetricScaleMeasurement",
                SCHEMA_VERSION,
                measurement_raw,
            )
        )
        import_spec = load_default_operator_specs()["metric_scale_measurement_import@1"]
        validate_operator_outputs(import_spec, {"measurement": measurement}, store)
        import_attempt.status = "succeeded"
        import_attempt.finished_at = utc_now()
        import_attempt.outputs = {"measurement": measurement}
        run.inputs["measurement"] = measurement
        attempt = NodeAttempt(
            "calibrate_metric_scale",
            1,
            "metric_scale_calibration@1",
            "core",
            "running",
            "executed",
            utc_now(),
            None,
            None,
        )
        run.node_attempts.append(attempt)
        _persist_build_run(store, run)
        if output_path.exists():
            raise FileExistsError(output_path)
        if not store.verify_digest(release):
            raise ContractError("source release has an invalid digest")
        release_raw = store.read_structured(release)
        source_files = _release_files(store, release)
        generated_paths = {
            "qa/metric-scale-report.json",
            "evidence/metric-scale-measurement.json",
            "provenance/metric-scale-visual.json",
            "provenance/metric-scale-collision.json",
            "provenance/metric-scale-quality.json",
            "provenance/metric-scale-asset.json",
            "provenance/metric-scale-visual-export.json",
            "provenance/metric-scale-collision-export.json",
        }
        conflicts = generated_paths & source_files.keys()
        for generated in generated_paths:
            path = PurePosixPath(generated)
            conflicts.update(
                name
                for name in source_files
                if PurePosixPath(name) in path.parents or path in PurePosixPath(name).parents
            )
        if conflicts:
            raise ContractError(
                f"source release conflicts with metric output paths: {sorted(conflicts)}"
            )
        source_asset = _artifact(release_raw.get("asset_definition"), "asset_definition")
        if not store.verify_digest(source_asset):
            raise ContractError("source AssetDefinition has an invalid digest")
        asset = store.read_structured(source_asset)
        spatial = asset.get("spatial")
        geometry = asset.get("geometry")
        if not isinstance(spatial, dict) or not isinstance(geometry, dict):
            raise ContractError("source AssetDefinition spatial/geometry is invalid")
        if spatial.get("unit") != "relative_unit" or spatial.get("scale_status") != "relative":
            raise ContractError("metric calibration requires a relative-scale source asset")
        if asset.get("physics") is not None:
            raise ContractError("metric calibration v1 requires physics to be null")
        if spatial.get("obb") is not None:
            raise ContractError("metric calibration v1 does not support a source OBB")
        visuals = geometry.get("visual_meshes")
        collisions = geometry.get("collision_meshes")
        if geometry.get("point_clouds") or geometry.get("gaussians"):
            raise ContractError(
                "metric calibration v1 rejects point clouds and gaussians until they are scaled"
            )
        if not isinstance(visuals, list) or len(visuals) != 1 or not isinstance(collisions, list):
            raise ContractError("metric calibration requires one visual mesh and valid collisions")
        visual = _artifact(visuals[0], "visual_meshes[0]")
        collision = _artifact(collisions[0], "collision_meshes[0]") if collisions else None
        if len(collisions) > 1:
            raise ContractError("metric calibration v1 supports at most one collision mesh")
        if (
            measurement_raw["source_asset_id"] != source_asset.artifact_id
            or measurement_raw["visual_artifact_id"] != visual.artifact_id
            or measurement_raw["frame_id"] != spatial.get("canonical_frame_id")
        ):
            raise ContractError("metric measurement does not match the selected asset and frame")
        for reference, kind in ((visual, "triangle_mesh"), (collision, "collision_mesh")):
            if reference is None:
                continue
            if (
                not store.verify_digest(reference)
                or store.get_manifest(reference.artifact_id).identity.kind != kind
            ):
                raise ContractError(f"source {kind} is invalid")
            metadata = store.get_manifest(reference.artifact_id).identity.identity_metadata
            if (
                metadata.get("frame_id") != spatial.get("canonical_frame_id")
                or metadata.get("unit") != "relative_unit"
                or metadata.get("up_axis") != "+Z"
                or metadata.get("forward_axis") != "+X"
            ):
                raise ContractError(f"source {kind} does not match canonical spatial contract")
        point_a = np.asarray(measurement_raw["point_a"], dtype=float)
        point_b = np.asarray(measurement_raw["point_b"], dtype=float)
        scale = measurement_raw["distance_meters"] / float(np.linalg.norm(point_b - point_a))
        visual_bytes, vertices = _scaled_glb(store, visual, scale)
        metadata = store.get_manifest(visual.artifact_id).identity.identity_metadata
        metric_visual = store.persist_bytes(
            visual_bytes,
            kind="triangle_mesh",
            schema_name="glTF",
            schema_version="2.0",
            identity_metadata={**metadata, "unit": "meter", "metric_scale_factor": scale},
        )
        metric_collision = None
        if collision is not None:
            collision_bytes, _ = _scaled_glb(store, collision, scale)
            collision_meta = store.get_manifest(collision.artifact_id).identity.identity_metadata
            metric_collision = store.persist_bytes(
                collision_bytes,
                kind="collision_mesh",
                schema_name="glTF",
                schema_version="2.0",
                identity_metadata={**collision_meta, "unit": "meter", "metric_scale_factor": scale},
            )
        spec = load_default_operator_specs()["metric_scale_calibration@1"]
        operator_inputs: dict[str, PortValue | list[PortValue]] = {
            "source_asset": source_asset,
            "measurement": measurement,
            "visual": visual,
        }
        if collision is not None:
            operator_inputs["collision"] = collision
        validate_operator_inputs(spec, operator_inputs, store)
        checks = [
            QualityCheck(
                "metric_scale",
                True,
                scale,
                "metric-scale-v1",
                "pass",
                evidence_artifacts=[measurement],
            ),
            QualityCheck(
                "geometry_loadable",
                True,
                len(vertices),
                "metric-scale-v1",
                "pass",
                evidence_artifacts=[metric_visual],
            ),
            QualityCheck(
                "spatial_contract",
                True,
                "meter",
                "metric-scale-v1",
                "pass",
                evidence_artifacts=[metric_visual],
            ),
        ]
        quality = store.persist_structured(
            StructuredValue(
                "quality_report",
                "QualityReport",
                SCHEMA_VERSION,
                to_primitive(QualityReport("metric-scale-v1", checks, "pass")),
            )
        )
        updated = dict(asset)
        updated_geometry = dict(geometry)
        updated_geometry["visual_meshes"] = [to_primitive(metric_visual)]
        updated_geometry["collision_meshes"] = (
            [] if metric_collision is None else [to_primitive(metric_collision)]
        )
        updated["geometry"] = updated_geometry
        updated_spatial = dict(spatial)
        updated_spatial.update(
            {
                "aabb": {
                    "minimum": vertices.min(axis=0).tolist(),
                    "maximum": vertices.max(axis=0).tolist(),
                },
                "unit": "meter",
                "scale_status": "metric",
            }
        )
        updated["spatial"] = updated_spatial
        components = updated.get("component_provenance")
        if not isinstance(components, list):
            raise ContractError("source component_provenance is invalid")
        remapped_components = []
        provenance_by_id = {}
        visual_lineage_ids = {visual.artifact_id}
        for reference in store.find_artifacts("provenance_record"):
            record = store.read_structured(reference)
            provenance_by_id[record.get("provenance_id")] = record
            if record.get("output_artifact_id") == visual.artifact_id:
                derived = record.get("derived_from_artifact_ids")
                if isinstance(derived, list):
                    visual_lineage_ids.update(item for item in derived if isinstance(item, str))
        for component in components:
            if not isinstance(component, dict):
                raise ContractError("source component_provenance entry is invalid")
            reference = _artifact(component.get("artifact"), "component_provenance artifact")
            if reference != visual:
                raise ContractError("component provenance must reference the canonical visual mesh")
            existing_ids = component.get("provenance_ids")
            if not isinstance(existing_ids, list) or not all(
                isinstance(item, str) for item in existing_ids
            ):
                raise ContractError("component provenance_ids are invalid")
            region_map = component.get("region_map")
            region_map_id = None
            if region_map is not None:
                region_map_ref = _artifact(region_map, "component region_map")
                if not store.verify_digest(region_map_ref):
                    raise ContractError("component region_map has an invalid digest")
                region_map_id = region_map_ref.artifact_id
            has_geometry_lineage = False
            for provenance_id in existing_ids:
                resolved_record = provenance_by_id.get(provenance_id)
                if resolved_record is None:
                    raise ContractError("component provenance_id does not resolve")
                output_artifact_id = resolved_record.get("output_artifact_id")
                if output_artifact_id in visual_lineage_ids:
                    has_geometry_lineage = True
                elif output_artifact_id != region_map_id:
                    raise ContractError("component provenance_id is unrelated to its geometry")
            if not has_geometry_lineage:
                raise ContractError("component provenance lacks geometry lineage")
            remapped_components.append(
                {
                    **component,
                    "artifact": to_primitive(metric_visual),
                    "provenance_ids": list(existing_ids),
                }
            )
        visual_provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id=attempt.node_id,
            port_name="visual",
            artifact=metric_visual,
            derived_from=[visual, measurement],
            operator="metric_scale_calibration",
            backend="core",
            backend_version="point-distance-v1",
            parameters={"scale_factor": scale},
            seed=None,
            source="derived",
        )
        visual_record = store.read_structured(visual_provenance)
        for component in remapped_components:
            component["provenance_ids"].append(visual_record["provenance_id"])
        updated["component_provenance"] = remapped_components
        quality_ids = updated.get("quality_report_ids")
        if not isinstance(quality_ids, list):
            raise ContractError("source quality_report_ids is invalid")
        updated["quality_report_ids"] = [*quality_ids, quality.artifact_id]
        identity = sha256_bytes(
            canonical_json_bytes(
                [source_asset.artifact_id, measurement.artifact_id, quality.artifact_id]
            )
        )
        updated["asset_id"] = f"asset_{identity.split(':', 1)[1]}"
        updated["asset_version"] = "1.0"
        asset_ref = store.persist_structured(
            StructuredValue("asset_definition", "AssetDefinition", SCHEMA_VERSION, updated)
        )
        outputs: dict[str, PortValue | list[PortValue]] = {
            "asset": asset_ref,
            "visual": metric_visual,
            "report": quality,
        }
        if metric_collision is not None:
            outputs["collision"] = metric_collision
        validate_operator_outputs(spec, outputs, store)
        collision_provenance = None
        if metric_collision is not None and collision is not None:
            collision_provenance = _persist_provenance(
                store,
                run_id=run.run_id,
                node_id=attempt.node_id,
                port_name="collision",
                artifact=metric_collision,
                derived_from=[collision, measurement],
                operator="metric_scale_calibration",
                backend="core",
                backend_version="point-distance-v1",
                parameters={"scale_factor": scale},
                seed=None,
                source="derived",
            )
        quality_provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id=attempt.node_id,
            port_name="report",
            artifact=quality,
            derived_from=[measurement, metric_visual]
            + ([metric_collision] if metric_collision else []),
            operator="metric_scale_calibration",
            backend="core",
            backend_version="point-distance-v1",
            parameters={"scale_factor": scale, "profile": "metric-scale-v1"},
            seed=None,
            source="derived",
        )
        asset_provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id=attempt.node_id,
            port_name="asset",
            artifact=asset_ref,
            derived_from=[source_asset, measurement, metric_visual, quality]
            + ([metric_collision] if metric_collision else []),
            operator="metric_scale_calibration",
            backend="core",
            backend_version="point-distance-v1",
            parameters={"scale_factor": scale},
            seed=None,
            source="derived",
        )
        visual_glb = store.persist_bytes(
            _export_glb(store, metric_visual),
            kind="gltf_asset",
            schema_name="glTF",
            schema_version="2.0",
            identity_metadata={
                "media_type": "model/gltf-binary",
                "frame_id": "gltf_export",
                "unit": "meter",
                "up_axis": "+Y",
                "forward_axis": "+Z",
                "export_profile": "gltf2-v1",
            },
        )
        export_attempt = NodeAttempt(
            "export_metric_scale",
            1,
            "metric_scale_export@1",
            "core",
            "running",
            "executed",
            utc_now(),
            None,
            None,
        )
        run.node_attempts.append(export_attempt)
        visual_export_provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id=export_attempt.node_id,
            port_name="visual_glb",
            artifact=visual_glb,
            derived_from=[metric_visual],
            operator="metric_scale_export",
            backend="core",
            backend_version="gltf2-v1",
            parameters={},
            seed=None,
            source="derived",
        )
        files = {
            "geometry/visual.glb": visual_glb,
            "qa/metric-scale-report.json": quality,
            "evidence/metric-scale-measurement.json": measurement,
            "provenance/metric-scale-visual.json": visual_provenance,
            "provenance/metric-scale-quality.json": quality_provenance,
            "provenance/metric-scale-asset.json": asset_provenance,
            "provenance/metric-scale-visual-export.json": visual_export_provenance,
        }
        if collision_provenance is not None:
            files["provenance/metric-scale-collision.json"] = collision_provenance
        collision_glb = None
        collision_export_provenance = None
        if metric_collision is not None:
            collision_glb = store.persist_bytes(
                _export_glb(store, metric_collision),
                kind="gltf_asset",
                schema_name="glTF",
                schema_version="2.0",
                identity_metadata={
                    "media_type": "model/gltf-binary",
                    "frame_id": "gltf_export",
                    "unit": "meter",
                    "up_axis": "+Y",
                    "forward_axis": "+Z",
                    "export_profile": "gltf2-collision-v1",
                },
            )
            files["geometry/collision.glb"] = collision_glb
            collision_export_provenance = _persist_provenance(
                store,
                run_id=run.run_id,
                node_id=export_attempt.node_id,
                port_name="collision_glb",
                artifact=collision_glb,
                derived_from=[metric_collision],
                operator="metric_scale_export",
                backend="core",
                backend_version="gltf2-collision-v1",
                parameters={},
                seed=None,
                source="derived",
            )
            files["provenance/metric-scale-collision-export.json"] = collision_export_provenance
        export_spec = load_default_operator_specs()["metric_scale_export@1"]
        export_inputs: dict[str, PortValue | list[PortValue]] = {"visual": metric_visual}
        export_outputs: dict[str, PortValue | list[PortValue]] = {"visual_glb": visual_glb}
        if metric_collision is not None and collision_glb is not None:
            export_inputs["collision"] = metric_collision
            export_outputs["collision_glb"] = collision_glb
        validate_operator_inputs(export_spec, export_inputs, store)
        validate_operator_outputs(export_spec, export_outputs, store)
        export_attempt.status = "succeeded"
        export_attempt.finished_at = utc_now()
        export_attempt.outputs = export_outputs
        for name, reference in source_files.items():
            if name not in {"geometry/visual.glb", "geometry/collision.glb"} and name not in files:
                files[name] = reference
        result_release = AssetRelease(asset_ref, files, "gltf2-v1")
        release_attempt = NodeAttempt(
            "derive_metric_scale_release",
            1,
            "metric_scale_release@1",
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
            derived_from=[release, asset_ref, visual_glb, quality]
            + ([collision_glb] if collision_glb else []),
            operator="metric_scale_release",
            backend="core",
            backend_version="1",
            parameters={"export_profile": "gltf2-v1"},
            seed=None,
            source="derived",
        )
        release_spec = load_default_operator_specs()["metric_scale_release@1"]
        release_inputs: dict[str, PortValue | list[PortValue]] = {
            "source_release": release,
            "asset": asset_ref,
            "visual_glb": visual_glb,
            "report": quality,
        }
        if collision_glb is not None:
            release_inputs["collision_glb"] = collision_glb
        validate_operator_inputs(release_spec, release_inputs, store)
        validate_operator_outputs(release_spec, {"release": release_ref}, store)
        release_attempt.status = "succeeded"
        release_attempt.finished_at = utc_now()
        release_attempt.outputs = {"release": release_ref}
        attempt.status = "succeeded"
        attempt.finished_at = utc_now()
        attempt.outputs = outputs
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
        final_run_path = store.blob_path(final_run_ref)
        target_run_path = staging_path / "run.json"
        temporary_run_path = target_run_path.with_name(f".run.{uuid.uuid4().hex}.tmp")
        temporary_run_path.write_bytes(final_run_path.read_bytes())
        os.replace(temporary_run_path, target_run_path)
        _publish_staged_release(staging_path, absolute_output)
        return MetricScaleResult(
            run.run_id,
            measurement,
            asset_ref,
            release_ref,
            metric_visual,
            metric_collision,
            quality,
            output_path.absolute(),
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

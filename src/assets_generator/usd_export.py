"""Traceable standard OpenUSD release export of an explicitly supplied rigid body."""

from __future__ import annotations

import json
import shutil
import struct
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .artifact_store import ArtifactStoreError, LocalArtifactStore
from .collision import _artifact
from .contracts import ContractError, validate_operator_inputs, validate_operator_outputs
from .errors import ErrorCode, classify_error
from .models import ArtifactRef, AssetRelease, BuildRun, NodeAttempt, PortValue, StructuredValue
from .operators import _load_scene
from .pipeline import load_default_operator_specs
from .rigid_body import (
    _mesh_instances,
    _publish_staged_release,
    _validated_collision_scene,
    validate_properties,
)
from .runtime import utc_now
from .scene_workflow import _release_files
from .serialization import to_primitive
from .workflow import _materialize_release, _persist_build_run, _persist_provenance


@dataclass(frozen=True)
class UsdExportResult:
    run_id: str
    asset_definition: ArtifactRef
    usd: ArtifactRef
    quality_report: ArtifactRef
    release_manifest: ArtifactRef
    output_directory: Path


def _checked(store: LocalArtifactStore, ref: ArtifactRef, kind: str) -> None:
    if not store.verify_digest(ref) or store.get_manifest(ref.artifact_id).identity.kind != kind:
        raise ContractError(f"invalid USD source {kind}")


def _check_glb_features(data: bytes) -> None:
    """Inspect features before trimesh can silently discard unsupported GLTF data."""
    if len(data) < 20 or data[:4] != b"glTF":
        raise ContractError("USD source must be binary glTF")
    length, chunk_type = struct.unpack_from("<II", data, 12)
    if chunk_type != 0x4E4F534A or length > len(data) - 20:
        raise ContractError("invalid USD source GLB JSON chunk")
    raw = json.loads(data[20 : 20 + length])

    def has_extensions(value: Any) -> bool:
        if isinstance(value, dict):
            return bool(value.get("extensions")) or any(
                has_extensions(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(has_extensions(item) for item in value)
        return False

    if raw.get("extensionsUsed") or raw.get("extensionsRequired") or has_extensions(raw):
        raise ContractError("USD v1 does not support glTF extensions")
    if raw.get("animations") or raw.get("skins"):
        raise ContractError("USD v1 does not support animated or skinned geometry")
    for mesh in raw.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            if primitive.get("targets") or primitive.get("mode", 4) != 4:
                raise ContractError("USD v1 requires triangle primitives without morph targets")
            attributes = primitive.get("attributes", {})
            material_index = primitive.get("material")
            if "COLOR_0" in attributes and material_index is not None:
                raise ContractError("USD v1 cannot preserve combined vertex colors and materials")
            if set(attributes) - {"POSITION", "NORMAL", "TEXCOORD_0", "COLOR_0"}:
                raise ContractError("USD v1 encountered unsupported vertex attributes")
    for material in raw.get("materials", []):
        if material.get("normalTexture") or material.get("occlusionTexture"):
            raise ContractError("USD v1 does not support normal or occlusion textures")
    for material in raw.get("materials", []):
        pbr = material.get("pbrMetallicRoughness", {})
        if (
            pbr.get("metallicRoughnessTexture")
            or material.get("emissiveTexture")
            or any(material.get("emissiveFactor", [0, 0, 0]))
        ):
            raise ContractError("USD v1 encountered unsupported PBR textures or emission")
        if material.get("alphaMode", "OPAQUE") != "OPAQUE":
            raise ContractError("USD v1 only supports opaque materials")
        if pbr.get("baseColorTexture", {}).get("texCoord", 0) != 0:
            raise ContractError("USD v1 only supports the first UV set")
    for sampler in raw.get("samplers", []):
        if "magFilter" in sampler or "minFilter" in sampler:
            raise ContractError("USD v1 cannot preserve explicit texture filtering")
        if sampler.get("wrapS", 10497) != 10497 or sampler.get("wrapT", 10497) != 10497:
            raise ContractError("USD v1 only supports repeat texture wrapping")
    for image in raw.get("images", []):
        if "uri" in image and not image["uri"].startswith("data:"):
            raise ContractError("USD source GLB must not reference external images")


def _source(
    store: LocalArtifactStore, release: ArtifactRef
) -> tuple[ArtifactRef, ArtifactRef, ArtifactRef, dict[str, Any], dict[str, ArtifactRef]]:
    _checked(store, release, "asset_release")
    release_raw = store.read_structured(release)
    files = _release_files(store, release)
    asset_ref = _artifact(release_raw.get("asset_definition"), "asset_definition")
    _checked(store, asset_ref, "asset_definition")
    asset = store.read_structured(asset_ref)
    spatial, geometry, physics = asset.get("spatial"), asset.get("geometry"), asset.get("physics")
    if (
        not isinstance(spatial, dict)
        or not isinstance(geometry, dict)
        or not isinstance(physics, dict)
    ):
        raise ContractError("USD export requires a metric rigid AssetDefinition")
    if spatial.get("unit") != "meter" or spatial.get("scale_status") != "metric":
        raise ContractError("USD export requires meter-scale geometry")
    if geometry.get("gaussians") or geometry.get("point_clouds"):
        raise ContractError("USD v1 does not support gaussians or point clouds")
    refs = []
    for field, kind in (("visual_meshes", "triangle_mesh"), ("collision_meshes", "collision_mesh")):
        values = geometry.get(field)
        if not isinstance(values, list) or len(values) != 1:
            raise ContractError(f"USD export requires exactly one {field}")
        ref = _artifact(values[0], field)
        _checked(store, ref, kind)
        metadata = store.get_manifest(ref.artifact_id).identity.identity_metadata
        if any(
            metadata.get(key) != value
            for key, value in {
                "frame_id": spatial.get("canonical_frame_id"),
                "unit": "meter",
                "up_axis": "+Z",
                "forward_axis": "+X",
            }.items()
        ) or not spatial.get("canonical_frame_id"):
            raise ContractError("USD geometry does not match canonical meter space")
        _mesh_instances(store, ref)
        refs.append(ref)
    visual, collision = refs
    _validated_collision_scene(store, collision)
    evidence = _artifact(physics.get("evidence"), "physics.evidence")
    _checked(store, evidence, "rigid_body_properties")
    evidence_identity = store.get_manifest(evidence.artifact_id).identity
    if (evidence_identity.schema_name, evidence_identity.schema_version) != (
        "RigidBodyProperties",
        "1.0",
    ):
        raise ContractError("unsupported rigid-body evidence schema")
    properties = validate_properties(store.read_structured(evidence))
    expected = {
        key: value
        for key, value in properties.items()
        if key not in {"source_release_id", "source_asset_id", "collision_artifact_id"}
    }
    expected.update(collision_mesh=to_primitive(collision), evidence=to_primitive(evidence))
    if (
        physics != expected
        or properties["collision_artifact_id"] != collision.artifact_id
        or properties["frame_id"] != spatial["canonical_frame_id"]
    ):
        raise ContractError("USD physics does not match its supplied evidence")
    parent_release = ArtifactRef(properties["source_release_id"])
    parent_asset = ArtifactRef(properties["source_asset_id"])
    _checked(store, parent_release, "asset_release")
    _checked(store, parent_asset, "asset_definition")
    if store.read_structured(parent_release).get("asset_definition") != to_primitive(parent_asset):
        raise ContractError("rigid-body evidence parent release does not match parent asset")
    _release_files(store, parent_release)
    parent = store.read_structured(parent_asset)
    if parent.get("physics") is not None or any(
        parent.get(key) != asset.get(key)
        for key in (
            "geometry",
            "appearance",
            "spatial",
            "semantics",
            "source_observation_ids",
            "component_provenance",
        )
    ):
        raise ContractError(
            "rigid-body asset differs from the evidence parent geometry or semantics"
        )
    verified = False
    for ref in files.values():
        if store.get_manifest(ref.artifact_id).identity.kind != "provenance_record":
            continue
        record = store.read_structured(ref)
        if (
            record.get("operator") != "rigid_body_assignment"
            or record.get("output_artifact_id") != asset_ref.artifact_id
        ):
            continue
        if not {parent_asset.artifact_id, collision.artifact_id, evidence.artifact_id} <= set(
            record.get("derived_from_artifact_ids", [])
        ):
            continue
        try:
            run = store.get_build_run(record["run_id"])
        except (ArtifactStoreError, KeyError, OSError, ValueError):
            continue
        if (
            run.get("status") != "succeeded"
            or run.get("inputs", {}).get("release") != to_primitive(parent_release)
            or run.get("inputs", {}).get("properties") != to_primitive(evidence)
        ):
            continue
        verified = any(
            attempt.get("node_id") == record.get("node_id")
            and attempt.get("operator") == "rigid_body_assignment@1"
            and attempt.get("status") == "succeeded"
            and attempt.get("outputs", {}).get("asset") == to_primitive(asset_ref)
            for attempt in run.get("node_attempts", [])
        )
        if verified:
            break
    if not verified:
        raise ContractError("USD physics lacks successful rigid-body assignment provenance")
    _check_glb_features(store.blob_path(visual).read_bytes())
    return asset_ref, visual, collision, physics, files


def export_usd(*, release: ArtifactRef, store_path: Path, output_path: Path) -> UsdExportResult:
    """Export without changing the source AssetDefinition or inferring physics."""
    from .usd_writer import sdk_environment_identity, write_usd

    store = LocalArtifactStore(store_path)
    attempt = NodeAttempt(
        "export_usd", 1, "usd_export@1", "openusd", "running", "executed", utc_now(), None, None
    )
    run = BuildRun(
        f"run_{uuid.uuid4().hex}",
        "usd_export_v1",
        "1",
        "running",
        {"release": release},
        [attempt],
        utc_now(),
        None,
    )
    _persist_build_run(store, run)
    staging: Path | None = None
    try:
        absolute_output = output_path.expanduser().absolute()
        if absolute_output.exists():
            raise FileExistsError(absolute_output)
        asset, visual, collision, physics, files = _source(store, release)
        reserved = {"geometry/usd", "qa/usd-report.json", "provenance/usd"}
        for name in files:
            source_path = PurePosixPath(name)
            if any(
                source_path == PurePosixPath(value)
                or source_path in PurePosixPath(value).parents
                or PurePosixPath(value) in source_path.parents
                for value in reserved
            ):
                raise ContractError("source release conflicts with USD output paths")
        spec = load_default_operator_specs()["usd_export@1"]
        validate_operator_inputs(spec, {"asset": asset, "release": release}, store)
        environment = sdk_environment_identity()
        parameters = {"export_profile": "openusd-rigid-v1", "environment": environment}
        new_files = dict(files)
        textures = []
        with tempfile.TemporaryDirectory(prefix="assets-usd-") as directory:
            folder = Path(directory)
            summary = write_usd(
                visual=_load_scene(store.blob_path(visual).read_bytes()),
                collision=_load_scene(store.blob_path(collision).read_bytes()),
                physics=physics,
                output_path=folder / "asset.usda",
            )
            if not isinstance(summary, dict) or summary.get("status") != "pass":
                raise ContractError("USD roundtrip QA failed")
            for path in sorted(folder.rglob("*")):
                if path.is_symlink():
                    raise ContractError("USD writer emitted a symlink")
                if not path.is_file() or path.name == "asset.usda" and path.parent == folder:
                    continue
                relative = path.relative_to(folder).as_posix()
                if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                    raise ContractError("USD writer emitted an unsupported auxiliary file")
                ref = store.persist_bytes(
                    path.read_bytes(),
                    kind="texture_2d",
                    schema_name="raster_image",
                    schema_version="1.0",
                    identity_metadata={
                        "media_type": "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
                    },
                )
                textures.append(ref)
                new_files[f"geometry/usd/{relative}"] = ref
                provenance = _persist_provenance(
                    store,
                    run_id=run.run_id,
                    node_id=attempt.node_id,
                    port_name="textures",
                    element_id=relative,
                    artifact=ref,
                    derived_from=[visual],
                    operator="usd_export",
                    backend="openusd",
                    backend_version="1",
                    parameters=parameters,
                    seed=None,
                    source="derived",
                )
                new_files[f"provenance/usd/texture-{len(textures)}.json"] = provenance
            usd = store.persist_bytes(
                (folder / "asset.usda").read_bytes(),
                kind="usd_asset",
                schema_name="OpenUSD",
                schema_version="1.0",
                identity_metadata={
                    "media_type": "model/vnd.usda",
                    "frame_id": physics["frame_id"],
                    "unit": "meter",
                    "up_axis": "+Z",
                    "forward_axis": "+X",
                    "export_profile": "openusd-rigid-v1",
                },
            )
        evidence = _artifact(physics["evidence"], "physics.evidence")
        usd_provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id=attempt.node_id,
            port_name="usd",
            artifact=usd,
            derived_from=[release, asset, visual, collision, evidence, *textures],
            operator="usd_export",
            backend="openusd",
            backend_version="1",
            parameters=parameters,
            seed=None,
            source="derived",
        )
        report = store.persist_structured(
            StructuredValue(
                "quality_report",
                "QualityReport",
                "1.0",
                {
                    "profile": "openusd-rigid-v1",
                    "overall_status": "pass",
                    "checks": [
                        {
                            "check_id": "usd_roundtrip",
                            "applicable": True,
                            "status": "pass",
                            "value": "validated",
                            "threshold_profile": "openusd-rigid-v1",
                            "reason": None,
                            "evidence_artifacts": [to_primitive(usd), to_primitive(evidence)],
                        }
                    ],
                    "roundtrip": summary,
                },
            )
        )
        quality_provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id=attempt.node_id,
            port_name="report",
            artifact=report,
            derived_from=[usd, asset, evidence, *textures],
            operator="usd_export",
            backend="openusd",
            backend_version="1",
            parameters=parameters,
            seed=None,
            source="derived",
        )
        outputs: dict[str, PortValue | list[PortValue]] = {
            "usd": usd,
            "report": report,
            "textures": list(textures),
        }
        validate_operator_outputs(spec, outputs, store)
        attempt.outputs = outputs
        attempt.status, attempt.finished_at = "succeeded", utc_now()
        new_files.update(
            {
                "geometry/usd/asset.usda": usd,
                "qa/usd-report.json": report,
                "provenance/usd/asset.json": usd_provenance,
                "provenance/usd/quality.json": quality_provenance,
            }
        )
        release_attempt = NodeAttempt(
            "derive_usd_release",
            1,
            "usd_release@1",
            "core",
            "running",
            "executed",
            utc_now(),
            None,
            None,
        )
        run.node_attempts.append(release_attempt)
        result_release = AssetRelease(asset, new_files, "openusd-rigid-v1")
        result_ref = store.persist_structured(
            StructuredValue("asset_release", "AssetRelease", "1.0", to_primitive(result_release))
        )
        release_spec = load_default_operator_specs()["usd_release@1"]
        validate_operator_inputs(
            release_spec,
            {"source_release": release, "asset": asset, "usd": usd, "report": report},
            store,
        )
        validate_operator_outputs(release_spec, {"release": result_ref}, store)
        _persist_provenance(
            store,
            run_id=run.run_id,
            node_id=release_attempt.node_id,
            port_name="release",
            artifact=result_ref,
            derived_from=[release, asset, usd, report, *textures],
            operator="usd_release",
            backend="core",
            backend_version="1",
            parameters={"export_profile": "openusd-rigid-v1"},
            seed=None,
            source="derived",
        )
        release_attempt.outputs = {"release": result_ref}
        release_attempt.status, release_attempt.finished_at = "succeeded", utc_now()
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
        staging = absolute_output.parent / f".{absolute_output.name}.{uuid.uuid4().hex}.staged"
        _materialize_release(
            store, staging, result_ref, result_release, _persist_build_run(store, run)
        )
        materialize.status, materialize.finished_at = "succeeded", utc_now()
        run.status, run.finished_at = "succeeded", materialize.finished_at
        final_run = _persist_build_run(store, run)
        (staging / "run.json").write_bytes(store.blob_path(final_run).read_bytes())
        _publish_staged_release(staging, absolute_output)
        return UsdExportResult(run.run_id, asset, usd, report, result_ref, absolute_output)
    except Exception as error:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        active = run.node_attempts[-1]
        active.status, active.finished_at = "failed", utc_now()
        active.error_code = (
            ErrorCode.RELEASE_FAILED.value
            if active.node_id == "materialize_release"
            else classify_error(error).value
        )
        run.status, run.finished_at = "failed", active.finished_at
        _persist_build_run(store, run)
        raise

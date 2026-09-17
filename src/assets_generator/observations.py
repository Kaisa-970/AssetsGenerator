from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image

from .artifact_store import LocalArtifactStore
from .contracts import ContractError
from .models import (
    SCHEMA_VERSION,
    ArtifactRef,
    CameraRecord,
    Confidence,
    ObservationBundle,
    ObservationView,
    SpatialTransform,
    StructuredValue,
)
from .serialization import canonical_json_bytes, sha256_bytes, to_primitive


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class ObservationRasterInfo:
    schema_name: str
    media_type: str
    mode: str
    size: tuple[int, int]


def inspect_observation_raster(path: Path, *, kind: str, label: str) -> ObservationRasterInfo:
    try:
        with Image.open(path) as image:
            image.load()
            mode = image.mode
            image_format = image.format
            if kind == "rgb_image" and mode != "RGB":
                raise ContractError(f"{label} rgb_image must use RGB mode")
            if kind == "binary_mask":
                if mode not in {"1", "L"}:
                    raise ContractError(f"{label} binary_mask must be single-channel")
                values = {
                    value for value, count in enumerate(image.convert("L").histogram()) if count
                }
                if not values <= {0, 255}:
                    raise ContractError(f"{label} binary_mask must contain only 0 and 255")
                if 255 not in values:
                    raise ContractError(f"{label} binary_mask must contain foreground pixels")
            if kind == "depth_map" and mode not in {"I", "I;16", "I;16B", "I;16L", "F"}:
                raise ContractError(f"{label} depth_map must be a single-channel numeric raster")
            normalized_format = image_format.lower() if image_format else "raster"
            return ObservationRasterInfo(
                normalized_format,
                Image.MIME.get(image_format or "", "application/octet-stream"),
                mode,
                image.size,
            )
    except OSError as error:
        raise ContractError(f"{label} is not a readable image: {error}") from error


def _validate_confidence(confidence: Confidence, camera_id: str) -> None:
    if not _finite_number(confidence.value):
        raise ContractError(f"camera {camera_id} confidence value must be finite")
    if (
        not isinstance(confidence.method, str)
        or not confidence.method
        or not isinstance(confidence.method_version, str)
        or not confidence.method_version
    ):
        raise ContractError(f"camera {camera_id} confidence requires method and method_version")
    if confidence.calibration_domain is not None and not isinstance(
        confidence.calibration_domain, str
    ):
        raise ContractError(f"camera {camera_id} confidence calibration_domain must be a string")
    if not isinstance(confidence.evidence_ids, list) or not all(
        isinstance(item, str) for item in confidence.evidence_ids
    ):
        raise ContractError(f"camera {camera_id} confidence evidence_ids must be strings")


def validate_camera_record(camera: CameraRecord, view: ObservationView) -> None:
    if not isinstance(camera.camera_id, str) or not camera.camera_id:
        raise ContractError("camera_id must not be empty")
    if not isinstance(camera.image_view_id, str) or camera.image_view_id != view.view_id:
        raise ContractError(
            f"camera {camera.camera_id} references view {camera.image_view_id}, "
            f"expected {view.view_id}"
        )
    if (
        not isinstance(camera.width, int)
        or isinstance(camera.width, bool)
        or not isinstance(camera.height, int)
        or isinstance(camera.height, bool)
        or camera.width <= 0
        or camera.height <= 0
    ):
        raise ContractError(f"camera {camera.camera_id} dimensions must be positive")
    if not isinstance(camera.distortion, list):
        raise ContractError(f"camera {camera.camera_id} distortion must be a list")
    intrinsics = (camera.fx, camera.fy, camera.cx, camera.cy, *camera.distortion)
    if not all(_finite_number(value) for value in intrinsics):
        raise ContractError(f"camera {camera.camera_id} contains non-finite calibration values")
    if camera.fx <= 0 or camera.fy <= 0:
        raise ContractError(f"camera {camera.camera_id} focal lengths must be positive")
    if camera.model not in {"pinhole", "opencv"}:
        raise ContractError(f"camera {camera.camera_id} has unsupported model {camera.model}")
    if not isinstance(camera.camera_frame_id, str) or not camera.camera_frame_id:
        raise ContractError(f"camera {camera.camera_id} requires camera_frame_id")
    if not isinstance(camera.source, str) or camera.source not in {"provided", "estimated"}:
        raise ContractError(f"camera {camera.camera_id} has invalid source {camera.source}")
    if camera.confidence is not None:
        if not isinstance(camera.confidence, Confidence):
            raise ContractError(f"camera {camera.camera_id} confidence must be a Confidence record")
        _validate_confidence(camera.confidence, camera.camera_id)
    if camera.score is not None and not _finite_number(camera.score):
        raise ContractError(f"camera {camera.camera_id} score must be finite")
    if camera.score_method is not None and not isinstance(camera.score_method, str):
        raise ContractError(f"camera {camera.camera_id} score_method must be a string")
    transform = camera.T_world_camera
    if transform is not None:
        if not isinstance(transform, SpatialTransform):
            raise ContractError(f"camera {camera.camera_id} transform must be a SpatialTransform")
        if transform.source_frame_id != camera.camera_frame_id:
            raise ContractError(
                f"camera {camera.camera_id} transform source must match camera_frame_id"
            )
        if (
            not isinstance(transform.target_frame_id, str)
            or not transform.target_frame_id
            or not isinstance(transform.matrix, list)
            or len(transform.matrix) != 4
            or any(not isinstance(row, list) or len(row) != 4 for row in transform.matrix)
        ):
            raise ContractError(f"camera {camera.camera_id} transform must be 4x4")
        if not all(_finite_number(value) for row in transform.matrix for value in row):
            raise ContractError(f"camera {camera.camera_id} transform contains non-finite values")
        matrix = np.asarray(transform.matrix, dtype=np.float64)
        if not np.allclose(matrix[3], [0.0, 0.0, 0.0, 1.0], atol=1e-8):
            raise ContractError(f"camera {camera.camera_id} transform must be homogeneous")
        rotation = matrix[:3, :3]
        if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6) or not np.isclose(
            np.linalg.det(rotation), 1.0, atol=1e-6
        ):
            raise ContractError(f"camera {camera.camera_id} transform must be rigid")


def _observation_id(views: list[ObservationView]) -> str:
    identity = sha256_bytes(canonical_json_bytes({"views": to_primitive(views)}))
    return f"observation_{identity.split(':', 1)[1]}"


def _normalize_camera_record(camera: CameraRecord) -> CameraRecord:
    transform = camera.T_world_camera
    normalized_transform = (
        replace(transform, matrix=[[float(value) for value in row] for row in transform.matrix])
        if transform is not None
        else None
    )
    confidence = camera.confidence
    normalized_confidence = (
        replace(confidence, value=float(confidence.value)) if confidence is not None else None
    )
    return replace(
        camera,
        fx=float(camera.fx),
        fy=float(camera.fy),
        cx=float(camera.cx),
        cy=float(camera.cy),
        distortion=[float(value) for value in camera.distortion],
        T_world_camera=normalized_transform,
        confidence=normalized_confidence,
        score=float(camera.score) if camera.score is not None else None,
    )


def _normalize_views(views: list[ObservationView]) -> list[ObservationView]:
    normalized = []
    for view in views:
        if view.camera is None:
            normalized.append(view)
            continue
        validate_camera_record(view.camera, view)
        normalized.append(replace(view, camera=_normalize_camera_record(view.camera)))
    return normalized


def validate_observation_bundle(bundle: ObservationBundle, store: LocalArtifactStore) -> None:
    if not bundle.views:
        raise ContractError("observation bundle requires at least one view")
    view_ids = [view.view_id for view in bundle.views]
    if any(not view_id for view_id in view_ids):
        raise ContractError("observation view_id must not be empty")
    if len(set(view_ids)) != len(view_ids):
        raise ContractError("observation bundle view_id values must be unique")
    camera_ids = [view.camera.camera_id for view in bundle.views if view.camera is not None]
    if len(set(camera_ids)) != len(camera_ids):
        raise ContractError("observation bundle camera_id values must be unique")
    world_frame_ids = {
        view.camera.T_world_camera.target_frame_id
        for view in bundle.views
        if view.camera is not None and view.camera.T_world_camera is not None
    }
    if len(world_frame_ids) > 1:
        raise ContractError("observation bundle cameras must share one world frame")
    for view in bundle.views:
        if not store.verify_digest(view.image):
            raise ContractError(f"view {view.view_id} image has invalid artifact digest")
        image = store.get_manifest(view.image.artifact_id).identity
        if image.kind != "rgb_image":
            raise ContractError(f"view {view.view_id} image must be rgb_image")
        image_size = inspect_observation_raster(
            store.blob_path(view.image), kind="rgb_image", label=f"view {view.view_id} image"
        ).size
        for field_name, reference, expected_kind in (
            ("mask", view.mask, "binary_mask"),
            ("depth", view.depth, "depth_map"),
        ):
            if reference is None:
                continue
            if not store.verify_digest(reference):
                raise ContractError(f"view {view.view_id} {field_name} has invalid artifact digest")
            identity = store.get_manifest(reference.artifact_id).identity
            if identity.kind != expected_kind:
                raise ContractError(f"view {view.view_id} {field_name} must be {expected_kind}")
            if field_name == "depth" and (
                not identity.identity_metadata.get("frame_id")
                or not identity.identity_metadata.get("unit")
            ):
                raise ContractError(f"view {view.view_id} depth requires frame_id and unit")
            if field_name == "depth":
                invalid_value = identity.identity_metadata.get("invalid_value")
                if invalid_value is not None and not _finite_number(invalid_value):
                    raise ContractError(
                        f"view {view.view_id} depth invalid_value must be finite numeric"
                    )
            aligned_size = inspect_observation_raster(
                store.blob_path(reference),
                kind=expected_kind,
                label=f"view {view.view_id} {field_name}",
            ).size
            if aligned_size != image_size:
                raise ContractError(
                    f"view {view.view_id} {field_name} dimensions differ: "
                    f"{aligned_size} != {image_size}"
                )
        if view.camera is not None:
            validate_camera_record(view.camera, view)
            if (view.camera.width, view.camera.height) != image_size:
                raise ContractError(
                    f"camera {view.camera.camera_id} dimensions differ from view {view.view_id}"
                )


def make_observation_bundle(
    views: list[ObservationView], store: LocalArtifactStore
) -> ObservationBundle:
    normalized_views = _normalize_views(views)
    provisional = ObservationBundle("", normalized_views)
    validate_observation_bundle(provisional, store)
    return replace(provisional, observation_id=_observation_id(normalized_views))


def observation_bundle_value(bundle: ObservationBundle) -> StructuredValue:
    return StructuredValue(
        "observation_bundle",
        "ObservationBundle",
        SCHEMA_VERSION,
        to_primitive(bundle),
    )


def _mapping(raw: object, label: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ContractError(f"{label} must be an object")
    return raw


def _string(raw: dict[str, object], name: str, label: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str):
        raise ContractError(f"{label}.{name} must be a string")
    return value


def _artifact_ref(raw: object, label: str) -> ArtifactRef:
    value = _mapping(raw, label)
    return ArtifactRef(_string(value, "artifact_id", label))


def _confidence(raw: object, label: str) -> Confidence:
    value = _mapping(raw, label)
    try:
        return Confidence(**value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ContractError(f"invalid {label}: {error}") from error


def camera_record_from_mapping(raw: object, label: str = "CameraRecord") -> CameraRecord:
    value = _mapping(raw, label)
    transform_raw = value.get("T_world_camera")
    confidence_raw = value.get("confidence")
    try:
        transform = (
            SpatialTransform(**_mapping(transform_raw, f"{label}.T_world_camera"))  # type: ignore[arg-type]
            if transform_raw is not None
            else None
        )
        confidence = (
            _confidence(confidence_raw, f"{label}.confidence")
            if confidence_raw is not None
            else None
        )
        return CameraRecord(
            **{**value, "T_world_camera": transform, "confidence": confidence}  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as error:
        raise ContractError(f"invalid {label}: {error}") from error


def observation_bundle_from_value(
    value: StructuredValue, store: LocalArtifactStore
) -> ObservationBundle:
    if (
        value.kind != "observation_bundle"
        or value.schema_name != "ObservationBundle"
        or value.schema_version != SCHEMA_VERSION
    ):
        raise ContractError("expected ObservationBundle structured value")
    payload = _mapping(value.value, "ObservationBundle")
    raw_views = payload.get("views")
    if not isinstance(raw_views, list):
        raise ContractError("ObservationBundle.views must be a list")
    views = []
    for index, raw_value in enumerate(raw_views):
        label = f"ObservationBundle.views[{index}]"
        raw = _mapping(raw_value, label)
        camera_raw = raw.get("camera")
        try:
            views.append(
                ObservationView(
                    _string(raw, "view_id", label),
                    _artifact_ref(raw.get("image"), f"{label}.image"),
                    _artifact_ref(raw["mask"], f"{label}.mask")
                    if raw.get("mask") is not None
                    else None,
                    _artifact_ref(raw["depth"], f"{label}.depth")
                    if raw.get("depth") is not None
                    else None,
                    camera_record_from_mapping(camera_raw, f"{label}.camera")
                    if camera_raw is not None
                    else None,
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ContractError(f"invalid {label}: {error}") from error
    observation_id = _string(payload, "observation_id", "ObservationBundle")
    views = _normalize_views(views)
    bundle = ObservationBundle(observation_id, views)
    validate_observation_bundle(bundle, store)
    expected_id = _observation_id(views)
    if observation_id != expected_id:
        raise ContractError("ObservationBundle observation_id does not match its views")
    return bundle


def observation_bundle_from_artifact(
    reference: ArtifactRef, store: LocalArtifactStore
) -> ObservationBundle:
    if not store.verify_digest(reference):
        raise ContractError("ObservationBundle has invalid artifact digest")
    manifest = store.get_manifest(reference.artifact_id)
    return observation_bundle_from_value(
        StructuredValue(
            manifest.identity.kind,
            manifest.identity.schema_name,
            manifest.identity.schema_version,
            store.read_structured(reference),
        ),
        store,
    )

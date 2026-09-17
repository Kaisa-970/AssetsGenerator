from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .artifact_store import LocalArtifactStore
from .contracts import ContractError
from .models import ArtifactRef, CameraRecord, ObservationView
from .observations import (
    camera_record_from_mapping,
    inspect_observation_raster,
    make_observation_bundle,
    observation_bundle_value,
)


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _persist_raster(
    store: LocalArtifactStore,
    path: Path,
    *,
    kind: str,
    schema_name: str,
    identity_metadata: dict[str, Any] | None = None,
) -> ArtifactRef:
    if not path.is_file():
        raise ContractError(f"observation input does not exist: {path}")
    return store.persist_bytes(
        path.read_bytes(),
        kind=kind,
        schema_name=schema_name,
        schema_version="1.0",
        identity_metadata=identity_metadata,
    )


def _camera(raw: dict[str, Any] | None) -> CameraRecord | None:
    if raw is None:
        return None
    camera = camera_record_from_mapping(raw, "camera")
    if camera.source != "provided":
        raise ContractError("imported camera source must be provided")
    return camera


def _import_observation_manifest(
    manifest_path: Path,
    store: LocalArtifactStore,
) -> ArtifactRef:
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"invalid observation manifest: {error}") from error
    if not isinstance(raw, dict) or not isinstance(raw.get("views"), list):
        raise ContractError("observation manifest must contain a views list")
    views = []
    for item in raw["views"]:
        if not isinstance(item, dict) or not isinstance(item.get("view_id"), str):
            raise ContractError("each observation view requires a string view_id")
        if not isinstance(item.get("image"), str):
            raise ContractError(f"view {item['view_id']} requires a string image path")
        image_path = _resolve(manifest_path.parent, item["image"])
        image_info = inspect_observation_raster(image_path, kind="rgb_image", label=str(image_path))
        image = _persist_raster(
            store,
            image_path,
            kind="rgb_image",
            schema_name=image_info.schema_name,
            identity_metadata={
                "media_type": image_info.media_type,
                "channel_layout": image_info.mode,
            },
        )
        mask_path = None
        if item.get("mask") is not None:
            if not isinstance(item["mask"], str):
                raise ContractError(f"view {item['view_id']} mask path must be a string")
            mask_path = _resolve(manifest_path.parent, item["mask"])
            mask_info = inspect_observation_raster(
                mask_path, kind="binary_mask", label=str(mask_path)
            )
        mask = (
            _persist_raster(
                store,
                mask_path,
                kind="binary_mask",
                schema_name=mask_info.schema_name,
                identity_metadata={
                    "media_type": mask_info.media_type,
                    "channel_layout": mask_info.mode,
                },
            )
            if mask_path is not None
            else None
        )
        depth_raw = item.get("depth")
        depth = None
        if depth_raw is not None:
            if not isinstance(depth_raw, dict):
                raise ContractError(f"view {item['view_id']} depth must be an object")
            if not isinstance(depth_raw.get("path"), str):
                raise ContractError(f"view {item['view_id']} depth requires a string path")
            depth_path = _resolve(manifest_path.parent, depth_raw["path"])
            depth_info = inspect_observation_raster(
                depth_path, kind="depth_map", label=str(depth_path)
            )
            depth = _persist_raster(
                store,
                depth_path,
                kind="depth_map",
                schema_name=depth_info.schema_name,
                identity_metadata={
                    "media_type": depth_info.media_type,
                    "channel_layout": depth_info.mode,
                    "frame_id": depth_raw.get("frame_id"),
                    "unit": depth_raw.get("unit"),
                    "invalid_value": depth_raw.get("invalid_value"),
                },
            )
        camera_raw = item.get("camera")
        if camera_raw is not None and not isinstance(camera_raw, dict):
            raise ContractError(f"view {item['view_id']} camera must be an object")
        try:
            views.append(
                ObservationView(
                    item["view_id"],
                    image,
                    mask=mask,
                    depth=depth,
                    camera=_camera(camera_raw),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ContractError(f"invalid view {item['view_id']}: {error}") from error
    bundle = make_observation_bundle(views, store)
    return store.persist_structured(observation_bundle_value(bundle))


def import_observation_manifest(
    manifest_path: Path,
    store: LocalArtifactStore,
) -> ArtifactRef:
    with tempfile.TemporaryDirectory(prefix="assets-generator-observations-") as directory:
        staged_store = LocalArtifactStore(Path(directory))
        bundle_reference = _import_observation_manifest(manifest_path, staged_store)
        with store.transaction() as transaction:
            for path in staged_store.manifests_dir.rglob("*.json"):
                raw = json.loads(path.read_text(encoding="utf-8"))
                manifest = staged_store.get_manifest(str(raw["artifact_id"]))
                blob = transaction.put_blob(
                    staged_store.blob_path(ArtifactRef(manifest.artifact_id)).read_bytes()
                )
                if blob.digest != manifest.identity.blob_digest:
                    raise ContractError("staged observation blob digest mismatch")
                transaction.put_manifest(manifest)
            transaction.commit()
        return bundle_reference

"""Validate a DAG-uploaded RGB image and preserve its exact Artifact identity."""

from __future__ import annotations

from .artifact_store import LocalArtifactStore
from .comfy_output import validate_png
from .models import ArtifactRef
from .remote_protocol import RemoteRequest, _digest, decode_remote_json
from .remote_service_store import RemoteServiceStore
from .serialization import canonical_json_bytes, sha256_bytes


def import_remote_image(
    request: RemoteRequest,
    owner: RemoteServiceStore,
    store: LocalArtifactStore,
    *,
    recovery_only: bool = False,
) -> ArtifactRef:
    """First import may persist verified bytes; recovery only reads existing evidence."""
    payload = decode_remote_json(request.payload_json)
    if set(payload) != {"operation", "parameters", "input_blobs", "input_digest", "binding_digest"}:
        raise ValueError("invalid ComfyUI DAG request fields")
    if payload["operation"] != "image_transform@1" or not isinstance(payload["parameters"], dict):
        raise ValueError("invalid ComfyUI DAG operation or parameters")
    for name in ("input_digest", "binding_digest"):
        _digest(payload[name], name)
    uploads = payload["input_blobs"]
    if not isinstance(uploads, dict) or set(uploads) != {"image"}:
        raise ValueError("ComfyUI DAG requires exactly one image")
    descriptor = uploads["image"]
    if not isinstance(descriptor, dict) or set(descriptor) != {"artifact_id", "identity"}:
        raise ValueError("invalid ComfyUI DAG image descriptor")
    identity = descriptor["identity"]
    if not isinstance(identity, dict) or set(identity) != {
        "kind",
        "schema_name",
        "schema_version",
        "blob_digest",
        "identity_metadata",
    }:
        raise ValueError("invalid ComfyUI DAG image identity")
    if (identity["kind"], identity["schema_name"], identity["schema_version"]) != (
        "rgb_image",
        "png",
        "1.0",
    ):
        raise ValueError("ComfyUI DAG image requires RGB PNG")
    metadata = identity["identity_metadata"]
    if (
        not isinstance(metadata, dict)
        or metadata.get("media_type") != "image/png"
        or metadata.get("channel_layout", "RGB") != "RGB"
    ):
        raise ValueError("ComfyUI DAG image metadata mismatch")
    if sha256_bytes(canonical_json_bytes(identity)) != descriptor["artifact_id"]:
        raise ValueError("ComfyUI DAG image Artifact identity mismatch")
    ref = ArtifactRef(descriptor["artifact_id"])
    if owner.lookup(request) is None:
        raise ValueError("ComfyUI DAG request is not registered")
    if recovery_only:
        if not store.verify_digest(ref):
            raise ValueError("ComfyUI DAG input evidence missing; no repair")
        data = store.blob_path(ref).read_bytes()
    else:
        data = owner.get_blob(identity["blob_digest"])
    if len(data) > 32 * 1024 * 1024 or sha256_bytes(data) != identity["blob_digest"]:
        raise ValueError("ComfyUI DAG input exceeds limit or changed")
    try:
        validate_png(data, mode="RGB")
    except (OSError, ValueError) as error:
        raise ValueError("ComfyUI DAG image encoding invalid") from error
    if not recovery_only:
        imported = store.persist_bytes(
            data,
            kind=identity["kind"],
            schema_name="png",
            schema_version="1.0",
            identity_metadata=metadata,
        )
        if imported != ref:
            raise ValueError("ComfyUI DAG imported identity changed")
    return ref

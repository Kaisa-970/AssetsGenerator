"""Validate uploaded shape input before launching an independent model process."""

from __future__ import annotations

import io
import json

from PIL import Image

from .artifact_store import LocalArtifactStore
from .models import ArtifactRef
from .remote_protocol import RemoteRequest
from .remote_service_store import RemoteServiceStore
from .serialization import canonical_json_bytes, sha256_bytes


def import_shape_rgba(
    request: RemoteRequest, service: RemoteServiceStore, store: LocalArtifactStore
) -> ArtifactRef:
    """Reconstruct the exact prepared RGBA Artifact; never accept a caller file path."""
    payload = json.loads(request.payload_json)
    uploads = payload.get("input_blobs")
    if not isinstance(uploads, dict) or set(uploads) != {"rgba"}:
        raise ValueError("shape service requires exactly one rgba input blob")
    descriptor = uploads["rgba"]
    if not isinstance(descriptor, dict) or set(descriptor) != {"artifact_id", "identity"}:
        raise ValueError("invalid shape input descriptor")
    identity = descriptor["identity"]
    if not isinstance(identity, dict) or set(identity) != {
        "kind",
        "schema_name",
        "schema_version",
        "blob_digest",
        "identity_metadata",
    }:
        raise ValueError("invalid shape input identity")
    if (identity["kind"], identity["schema_name"], identity["schema_version"]) != (
        "rgba_image",
        "png",
        "1.0",
    ) or identity["identity_metadata"] != {"media_type": "image/png", "channel_layout": "RGBA"}:
        raise ValueError("shape input requires prepared RGBA PNG contract")
    if sha256_bytes(canonical_json_bytes(identity)) != descriptor["artifact_id"]:
        raise ValueError("shape input Artifact identity mismatch")
    data = service.get_blob(identity["blob_digest"])
    with Image.open(io.BytesIO(data)) as image:
        if image.format != "PNG" or image.mode != "RGBA" or image.width * image.height > 25_000_000:
            raise ValueError("shape input requires bounded RGBA PNG encoding")
        image.load()
        if image.getchannel("A").getextrema()[1] == 0:
            raise ValueError("shape input contains no foreground")
    ref = store.persist_bytes(
        data,
        kind=identity["kind"],
        schema_name=identity["schema_name"],
        schema_version=identity["schema_version"],
        identity_metadata=identity["identity_metadata"],
    )
    if ref.artifact_id != descriptor["artifact_id"]:
        raise ValueError("imported shape input identity differs")
    return ref

"""Upload a verified image under a content-derived name and verify its readback.

Uploading alone never submits a workflow. A returned receipt is an observation,
not a promise that the remote input file cannot later be modified.
"""

from __future__ import annotations

import uuid
from http.client import HTTPException
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request

from .artifact_store import LocalArtifactStore
from .comfy_http import ComfyClient
from .comfy_output import validate_png
from .comfy_submission import ComfySubmissionUnknown
from .models import ArtifactRef
from .remote_protocol import decode_remote_json
from .serialization import sha256_bytes


def upload_image(
    client: ComfyClient,
    store: LocalArtifactStore,
    ref: ArtifactRef,
    *,
    max_bytes: int = 32 * 1024 * 1024,
) -> dict[str, Any]:
    if type(max_bytes) is not int or not 0 < max_bytes <= 128 * 1024 * 1024:
        raise ValueError("invalid ComfyUI upload limit")
    if not store.verify_digest(ref):
        raise ValueError("ComfyUI input Artifact missing or corrupt")
    identity = store.get_manifest(ref.artifact_id).identity
    modes = {"rgb_image": "RGB", "rgba_image": "RGBA"}
    mode = modes.get(identity.kind)
    if (
        mode is None
        or identity.schema_name != "png"
        or identity.schema_version != "1.0"
        or identity.identity_metadata.get("media_type") != "image/png"
        or identity.identity_metadata.get("channel_layout", mode) != mode
    ):
        raise ValueError("ComfyUI input requires RGB/RGBA PNG Artifact")
    with store.blob_path(ref).open("rb") as source:
        data = source.read(max_bytes + 1)
    if len(data) > max_bytes or sha256_bytes(data) != identity.blob_digest:
        raise ValueError("ComfyUI input exceeds limit or changed during read")
    validate_png(data, mode=mode)
    name = "asset-" + identity.blob_digest.split(":")[1] + ".png"
    subfolder = "assets-generator"
    boundary = "assetsgenerator" + uuid.uuid4().hex
    fields = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        for key, value in (("type", "input"), ("subfolder", subfolder), ("overwrite", "false"))
    )
    body = (
        fields
        + (
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
            f'filename="{name}"\r\nContent-Type: image/png\r\n\r\n'
        ).encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    request = Request(
        client.endpoint + "/upload/image",
        data=body,
        method="POST",
        headers={
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "Accept": "application/json",
        },
    )
    transport = client.transport
    try:
        with transport.opener.open(request, timeout=transport.timeout) as response:
            if response.status != 200 or response.headers.get_content_type() != "application/json":
                raise ValueError("invalid ComfyUI upload response")
            raw = response.read(transport.max_response_bytes + 1)
            if len(raw) > transport.max_response_bytes:
                raise ValueError("ComfyUI upload response exceeds limit")
            receipt = decode_remote_json(raw)
            if not isinstance(receipt, dict) or any(
                receipt.get(key) != value
                for key, value in {"name": name, "subfolder": subfolder, "type": "input"}.items()
            ):
                raise ValueError("ComfyUI upload changed the assigned input location")
        # Do not trust an upload receipt or follow a service-provided URL.
        query = urlencode({"filename": name, "subfolder": subfolder, "type": "input"})
        with transport.opener.open(
            Request(client.endpoint + "/view?" + query, headers={"Accept": "image/png"}),
            timeout=transport.timeout,
        ) as response:
            if response.status != 200 or response.headers.get_content_type() != "image/png":
                raise ValueError("invalid ComfyUI input readback response")
            returned = response.read(max_bytes + 1)
            if returned != data:
                raise ValueError("ComfyUI uploaded input bytes differ")
    except (OSError, HTTPException, ValueError) as error:
        raise ComfySubmissionUnknown("ComfyUI upload unverified; do not submit workflow") from error
    return {
        "artifact_id": ref.artifact_id,
        "blob_digest": identity.blob_digest,
        "endpoint": client.endpoint,
        "filename": name,
        "subfolder": subfolder,
        "type": "input",
        "workflow_value": subfolder + "/" + name,
        "verification": "exact-byte-readback@1",
    }

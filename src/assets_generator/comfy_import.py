"""Import one fixed ComfyUI image with a precommitted Artifact identity.

An interrupted import can be adopted only if its original Artifact is complete.
Missing evidence is blocked, never redownloaded or regenerated in recovery.
"""

from __future__ import annotations

import json
from typing import Any

from .artifact_store import LocalArtifactStore
from .comfy_http import ComfyClient
from .comfy_output import image_query, validate_png
from .comfy_submission import ComfySubmissionJournal
from .models import ArtifactRef
from .serialization import canonical_json_bytes, sha256_bytes


def import_image(
    client: ComfyClient,
    journal: ComfySubmissionJournal,
    store: LocalArtifactStore,
    key: str,
    *,
    node: str,
    index: int,
    mode: str,
) -> ArtifactRef:
    record = journal.read(key)
    if record["deployment"].get("endpoint") != client.endpoint:
        raise ValueError("ComfyUI import endpoint mismatch")
    observation = journal.observation(key)
    if observation is None:
        raise ValueError("ComfyUI import requires fixed observation")
    query = image_query(observation, node, index)
    if mode not in {"RGB", "RGBA"}:
        raise ValueError("ComfyUI import requires RGB or RGBA")
    binding = {
        "store": str(store.root),
        "mode": mode,
        "query": query,
        "observation_digest": sha256_bytes(canonical_json_bytes(observation)),
        "deployment_digest": sha256_bytes(canonical_json_bytes(record["deployment"])),
    }
    # Serialize first imports; only the winner may download and reserve the identity.
    journal.db.execute("BEGIN IMMEDIATE")
    try:
        row = journal.db.execute(
            "SELECT receipt,digest FROM image_imports WHERE submission_key=? AND node=? AND slot=?",
            (key, node, index),
        ).fetchone()
        if row is not None:
            if sha256_bytes(bytes(row[0])) != row[1]:
                raise ValueError("ComfyUI import receipt corrupt")
            receipt = json.loads(row[0])
            if receipt["binding"] != binding:
                raise ValueError("ComfyUI import binding conflict")
            ref = ArtifactRef(receipt["artifact_id"])
            if not store.verify_digest(ref):
                raise ValueError("ComfyUI imported evidence missing or corrupt; recovery blocked")
            if sha256_bytes(canonical_json_bytes(receipt["identity"])) != ref.artifact_id:
                raise ValueError("ComfyUI import identity mismatch")
            journal.db.execute("COMMIT")
            return ref
        data = client.download_image(journal, key, node=node, index=index, mode=mode)
        validate_png(data, mode=mode)
        identity: dict[str, Any] = {
            "kind": "rgb_image" if mode == "RGB" else "rgba_image",
            "schema_name": "png",
            "schema_version": "1.0",
            "blob_digest": sha256_bytes(data),
            "identity_metadata": {"media_type": "image/png", "channel_layout": mode},
        }
        artifact_id = sha256_bytes(canonical_json_bytes(identity))
        receipt = {"binding": binding, "identity": identity, "artifact_id": artifact_id}
        body = canonical_json_bytes(receipt)
        journal.db.execute(
            "INSERT INTO image_imports VALUES (?, ?, ?, ?, ?)",
            (key, node, index, body, sha256_bytes(body)),
        )
        journal.db.execute("COMMIT")
    except BaseException:
        journal.db.execute("ROLLBACK")
        raise
    # Commit intent before Store writes, so a crash cannot silently select new bytes.
    ref = store.persist_bytes(
        data,
        kind=identity["kind"],
        schema_name="png",
        schema_version="1.0",
        identity_metadata=identity["identity_metadata"],
    )
    if ref.artifact_id != artifact_id:
        raise ValueError("ComfyUI imported Artifact differs from reserved identity")
    return ref

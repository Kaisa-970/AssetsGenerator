"""Read-only composite boundary evidence assembled from fixed submission/imports."""

from __future__ import annotations

import json
from typing import Any

from .artifact_store import LocalArtifactStore
from .comfy_output import image_query
from .comfy_submission import ComfySubmissionJournal
from .comfy_workflow import _validate_image_receipt
from .models import ArtifactRef, StructuredValue
from .serialization import canonical_json_bytes, sha256_bytes


def image_boundary_evidence(
    journal: ComfySubmissionJournal,
    store: LocalArtifactStore,
    key: str,
    *,
    node: str,
    index: int,
    mode: str,
) -> StructuredValue:
    """Build evidence without downloading, importing or repairing any Artifact.

    Deployment must contain the exact ComfyWorkflow.bind result as `workflow`.
    Internal implementation identities remain explicitly unverified. The caller
    must durably pin this value when publishing; this function is not a receipt.
    """
    record = journal.read(key)
    observation = journal.observation(key)
    if observation is None or observation["state"] != "succeeded":
        raise ValueError("composite evidence requires fixed successful observation")
    deployment = record["deployment"]
    workflow = deployment.get("workflow")
    if not isinstance(workflow, dict) or set(workflow) != {
        "prompt",
        "parameters",
        "images",
        "template_digest",
        "workflow_digest",
        "mapping_digest",
    }:
        raise ValueError("composite evidence requires full workflow binding")
    if (
        canonical_json_bytes(workflow["prompt"]) != canonical_json_bytes(record["prompt"])
        or workflow["workflow_digest"] != sha256_bytes(canonical_json_bytes(record["prompt"]))
        or not isinstance(workflow["images"], dict)
    ):
        raise ValueError("composite workflow binding mismatch")
    inputs = {}
    for name, receipt in workflow["images"].items():
        _validate_image_receipt(receipt, deployment.get("endpoint"))
        ref = ArtifactRef(receipt["artifact_id"])
        if not store.verify_digest(ref):
            raise ValueError("composite input evidence missing or corrupt")
        identity = store.get_manifest(ref.artifact_id).identity
        if identity.blob_digest != receipt["blob_digest"]:
            raise ValueError("composite input blob mismatch")
        inputs[name] = ref
    row = journal.db.execute(
        "SELECT receipt,digest FROM image_imports WHERE submission_key=? AND node=? AND slot=?",
        (key, node, index),
    ).fetchone()
    if row is None or sha256_bytes(bytes(row[0])) != row[1]:
        raise ValueError("composite import receipt missing or corrupt")
    receipt = json.loads(row[0])
    expected = {
        "store": str(store.root),
        "mode": mode,
        "query": image_query(observation, node, index),
        "observation_digest": sha256_bytes(canonical_json_bytes(observation)),
        "deployment_digest": sha256_bytes(canonical_json_bytes(deployment)),
    }
    if mode not in {"RGB", "RGBA"} or receipt["binding"] != expected:
        raise ValueError("composite output binding mismatch")
    output = ArtifactRef(receipt["artifact_id"])
    if sha256_bytes(
        canonical_json_bytes(receipt["identity"])
    ) != output.artifact_id or not store.verify_digest(output):
        raise ValueError("composite output evidence missing or corrupt")
    value: dict[str, Any] = {
        "provenance_scope": "composite_boundary_only",
        "submission": journal.submission_binding(key),
        "workflow": workflow,
        "deployment_claims": deployment,
        "internal_verification": {
            "comfy_revision": "unverified",
            "custom_nodes": "unverified",
            "models": "unverified",
        },
        "observation_digest": expected["observation_digest"],
        "inputs": inputs,
        "outputs": {"image": output},
        "output_mapping": {"node": node, "index": index, "mode": mode},
        "import_receipt_digest": row[1],
    }
    return StructuredValue("remote_job_result", "ComfyImageBoundary", "1.0", value)

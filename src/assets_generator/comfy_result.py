"""Precommit composite evidence identity; recover complete local evidence only."""

from __future__ import annotations

from .artifact_store import LocalArtifactStore
from .comfy_evidence import image_boundary_evidence
from .comfy_submission import ComfySubmissionJournal, ComfySubmissionUnknown
from .models import ArtifactRef
from .remote_protocol import RemoteJob, RemoteRequest
from .remote_service_store import RemoteServiceStore
from .serialization import canonical_json_bytes, sha256_bytes
from .workbench_persistence import _references


def recover_image_result(
    owner: RemoteServiceStore, request: RemoteRequest, store: LocalArtifactStore
) -> ArtifactRef:
    """Read pinned evidence offline, without needing the original ComfyUI journal."""
    try:
        reservation = owner.comfy_result(request)
        if reservation is None or reservation["store"] != str(store.root):
            raise ValueError("ComfyUI result reservation missing or Store changed")
        ref = ArtifactRef(reservation["artifact_id"])
        if not store.verify_digest(ref):
            raise ValueError("ComfyUI result evidence missing or corrupt")
        identity = store.get_manifest(ref.artifact_id).identity
        if (identity.kind, identity.schema_name, identity.schema_version) != (
            "remote_job_result",
            "ComfyImageBoundary",
            "1.0",
        ):
            raise ValueError("ComfyUI result schema mismatch")
        value = store.read_structured(ref)
        if value["submission"] != reservation["submission"] or value[
            "submission"
        ] != owner.comfy_binding(request):
            raise ValueError("ComfyUI result owner mismatch")
        pending = _references(value)
        seen = set()
        while pending:
            item = pending.pop()
            if item.artifact_id in seen:
                continue
            seen.add(item.artifact_id)
            if not store.verify_digest(item):
                raise ValueError("ComfyUI result dependency missing or corrupt")
            manifest = store.get_manifest(item.artifact_id)
            if manifest.identity.identity_metadata.get("media_type") == "application/json":
                pending.extend(
                    _references(
                        store.read_structured(item), schema_name=manifest.identity.schema_name
                    )
                )
        return ref
    except Exception as error:
        raise ComfySubmissionUnknown("ComfyUI result recovery blocked; no repair") from error


def fix_image_result(
    owner: RemoteServiceStore,
    request: RemoteRequest,
    journal: ComfySubmissionJournal,
    store: LocalArtifactStore,
    *,
    node: str,
    index: int,
    mode: str,
) -> ArtifactRef:
    """Reserve before writing; repeated calls adopt evidence, never regenerate it."""
    try:
        if owner.comfy_result(request) is not None:
            recovered = recover_image_result(owner, request, store)
            if store.read_structured(recovered)["output_mapping"] != {
                "node": node,
                "index": index,
                "mode": mode,
            }:
                raise ValueError("ComfyUI result output mapping changed")
            return recovered
        value = image_boundary_evidence(
            journal, store, request.submission_key, node=node, index=index, mode=mode
        )
        identity = {
            "kind": value.kind,
            "schema_name": value.schema_name,
            "schema_version": value.schema_version,
            "blob_digest": sha256_bytes(canonical_json_bytes(value.value)),
            "identity_metadata": {"media_type": "application/json"},
        }
        ref = ArtifactRef(sha256_bytes(canonical_json_bytes(identity)))
        reservation = {
            "store": str(store.root),
            "artifact_id": ref.artifact_id,
            "submission": value.value["submission"],
        }
        if owner.reserve_comfy_result(request, reservation):
            if store.persist_structured(value) != ref:
                raise ValueError("ComfyUI result differs from reservation")
        return recover_image_result(owner, request, store)
    except Exception as error:
        raise ComfySubmissionUnknown("ComfyUI result commit uncertain; no repair") from error


def publish_image_result(
    owner: RemoteServiceStore, request: RemoteRequest, store: LocalArtifactStore
) -> RemoteJob:
    """Publish the pinned image/evidence as downloadable service outputs, offline."""
    try:
        ref = recover_image_result(owner, request, store)
        value = store.read_structured(ref)
        output = ArtifactRef(value["outputs"]["image"]["artifact_id"])
        mapping = value["output_mapping"]
        identity = store.get_manifest(output.artifact_id).identity
        if (
            mapping["mode"] not in {"RGB", "RGBA"}
            or identity.kind != {"RGB": "rgb_image", "RGBA": "rgba_image"}[mapping["mode"]]
            or identity.schema_name != "png"
        ):
            raise ValueError("ComfyUI output image contract mismatch")
        from .comfy_output import validate_png

        image = store.blob_path(output).read_bytes()
        evidence = store.blob_path(ref).read_bytes()
        if (
            sha256_bytes(image) != identity.blob_digest
            or sha256_bytes(evidence) != store.get_manifest(ref.artifact_id).identity.blob_digest
        ):
            raise ValueError("ComfyUI evidence changed during publication")
        validate_png(image, mode=mapping["mode"])
        return owner.publish_comfy_outputs(
            request,
            evidence_artifact_id=ref.artifact_id,
            outputs={"image": (image, "image/png"), "evidence": (evidence, "application/json")},
        )
    except Exception as error:
        raise ComfySubmissionUnknown("ComfyUI publication blocked; no replay") from error

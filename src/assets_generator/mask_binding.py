"""Shared original-image binding validation for derived masks."""

from .artifact_store import LocalArtifactStore
from .contracts import ContractError
from .models import ArtifactRef


def validate_mask_image_binding(
    store: LocalArtifactStore, image: ArtifactRef, mask: ArtifactRef
) -> None:
    binding = store.get_manifest(mask.artifact_id).identity.identity_metadata.get(
        "selection_binding"
    )
    if binding is None:
        return  # Ordinary uploaded masks have no source-image claim.
    if not isinstance(binding, dict) or set(binding) != {"artifact_id"}:
        raise ContractError("invalid mask selection binding")
    ref = ArtifactRef(**binding)
    if not store.verify_digest(ref):
        raise ContractError("mask selection binding missing")
    identity = store.get_manifest(ref.artifact_id).identity
    if (identity.kind, identity.schema_name, identity.schema_version) not in {
        ("quality_evidence", "TextMaskSelection", "1.0"),
        ("quality_evidence", "TextMaskUnion", "1.0"),
    }:
        raise ContractError("unsupported mask selection binding")
    if store.read_structured(ref).get("image") != {"artifact_id": image.artifact_id}:
        raise ContractError("candidate mask must be used with its original image")

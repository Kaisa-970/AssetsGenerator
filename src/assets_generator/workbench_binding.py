"""Verified selection-to-model handoff; paths never constitute evidence."""

from __future__ import annotations

from typing import Any

from .artifact_store import LocalArtifactStore
from .completion import _checked
from .contracts import ContractError
from .instance_proposals import prepare_selection_mask
from .models import ArtifactRef, StructuredValue
from .operators import validate_binary_mask
from .serialization import to_primitive
from .workbench_models import MaskDraft


def create_selection_binding(
    store: LocalArtifactStore,
    *,
    original_proposals: ArtifactRef,
    selection: ArtifactRef,
    draft: MaskDraft,
) -> ArtifactRef:
    _checked(store, selection, "instance_selection")
    decision = store.read_structured(selection)
    if decision["selected_ids"] != [draft.proposal_id]:
        raise ContractError("workbench selection must contain exactly the confirmed proposal")
    actual_ref = ArtifactRef(**decision["proposals"])
    _checked(store, actual_ref, "instance_proposals")
    _checked(store, original_proposals, "instance_proposals")
    original = store.read_structured(original_proposals)
    actual = store.read_structured(actual_ref)
    image = ArtifactRef(**original["image"])
    if actual["image"] != original["image"]:
        raise ContractError("selection image changed")
    originals = {p["proposal_id"]: p for p in original["proposals"]}
    selected = {p["proposal_id"]: p for p in actual["proposals"]}[draft.proposal_id]
    original_mask = ArtifactRef(**originals[draft.proposal_id]["mask"])
    expected, _, _ = prepare_selection_mask(
        store, image, original_mask, invert=draft.invert, keep_largest=draft.keep_largest
    )
    final_mask = ArtifactRef(**selected["mask"])
    if final_mask != draft.final_mask or final_mask != expected:
        raise ContractError("selected mask differs from confirmed preview")
    if draft.invert or draft.keep_largest:
        transformation = actual.get("transformation", {})
        if transformation.get("source_proposals") != to_primitive(original_proposals):
            raise ContractError("processed proposals have no verified original lineage")
        if (
            transformation.get("rule_version") != draft.rule_version
            or transformation.get("invert") != draft.invert
            or transformation.get("keep_largest") != draft.keep_largest
        ):
            raise ContractError("mask transformation contract mismatch")
    elif actual_ref != original_proposals:
        raise ContractError("unchanged selection must use original proposals")
    return store.persist_structured(
        StructuredValue(
            "quality_evidence",
            "SelectionInputBinding",
            "1.0",
            {
                "schema_version": "1.0",
                "selection": to_primitive(selection),
                "original_proposals": to_primitive(original_proposals),
                "actual_proposals": to_primitive(actual_ref),
                "image": to_primitive(image),
                "original_mask": to_primitive(original_mask),
                "final_mask": to_primitive(final_mask),
                "draft": to_primitive(draft),
            },
        )
    )


def verify_imported_binding(
    store: LocalArtifactStore, binding: ArtifactRef, image: ArtifactRef, mask: ArtifactRef | None
) -> ArtifactRef:
    _checked(store, binding, "quality_evidence")
    identity = store.get_manifest(binding.artifact_id).identity
    if identity.schema_name != "SelectionInputBinding" or identity.schema_version != "1.0":
        raise ContractError("invalid selection binding schema")
    if mask is None:
        raise ContractError("selection binding requires an explicit mask")
    raw = store.read_structured(binding)
    comparisons: dict[str, Any] = {}
    for key, imported, kind in (("image", image, "rgb_image"), ("final_mask", mask, "binary_mask")):
        expected = ArtifactRef(**raw[key])
        _checked(store, expected, kind)
        _checked(store, imported, kind)
        original = store.get_manifest(expected.artifact_id).identity
        actual = store.get_manifest(imported.artifact_id).identity
        fields = ("width", "height", "channel_layout")
        if original.blob_digest != actual.blob_digest or any(
            original.identity_metadata.get(k) != actual.identity_metadata.get(k) for k in fields
        ):
            raise ContractError("actual model input differs from confirmed selection")
        comparisons[key] = {"expected": to_primitive(expected), "imported": to_primitive(imported)}
    validate_binary_mask(store, image, mask)
    return store.persist_structured(
        StructuredValue(
            "quality_evidence",
            "SelectionImportVerification",
            "1.0",
            {
                "binding": to_primitive(binding),
                "comparisons": comparisons,
                "method": "blob-digest-dimensions-channels-binary-v1",
            },
        )
    )

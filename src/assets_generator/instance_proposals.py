"""Publish automatic mask proposals and explicitly select them for object extraction."""

from __future__ import annotations

import math
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from PIL import Image
from scipy import ndimage  # type: ignore[import-untyped]

from .artifact_store import LocalArtifactStore
from .completion import _checked
from .contracts import ContractError, validate_operator_inputs, validate_operator_outputs
from .models import ArtifactRef, BuildRun, NodeAttempt, StructuredValue
from .operators import validate_binary_mask
from .pipeline import load_default_operator_specs
from .runtime import utc_now
from .serialization import canonical_json_bytes, to_primitive
from .workflow import _import_image, _persist_build_run, _persist_provenance


class InstanceProposer(Protocol):
    def propose(self, store: LocalArtifactStore, image: ArtifactRef) -> StructuredValue: ...


def _validate_result(
    store: LocalArtifactStore, image: ArtifactRef, value: StructuredValue
) -> list[ArtifactRef]:
    raw = value.value
    if raw.get("image") != to_primitive(image) or raw.get("status") != "unreviewed_proposals":
        raise ContractError(
            "instance proposals must reference the requested image and remain unreviewed"
        )
    proposals = raw.get("proposals")
    metadata = raw.get("backend_metadata")
    digest = metadata.get("checkpoint_digest") if isinstance(metadata, dict) else None
    if (
        not isinstance(proposals, list)
        or not isinstance(digest, str)
        or not digest.startswith("sha256:")
    ):
        raise ContractError("instance proposals require bounded proposals and checkpoint identity")
    ids = set()
    masks = []
    image_meta = store.get_manifest(image.artifact_id).identity.identity_metadata
    for proposal in proposals:
        identifier = proposal.get("proposal_id") if isinstance(proposal, dict) else None
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}|[A-Za-z0-9_-]{1,128}", identifier)
            or identifier in ids
        ):
            raise ContractError("proposal_id must be unique and path-safe")
        ids.add(identifier)
        mask = ArtifactRef(**proposal["mask"])
        _checked(store, mask, "binary_mask")
        validate_binary_mask(store, image, mask)
        meta = store.get_manifest(mask.artifact_id).identity.identity_metadata
        if (meta.get("width"), meta.get("height")) != (
            image_meta.get("width"),
            image_meta.get("height"),
        ):
            raise ContractError("proposal mask size must match requested image")
        for score in (proposal.get("predicted_iou"), proposal.get("stability_score")):
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
            ):
                raise ContractError("proposal scores must be finite")
        if proposal.get("label") != "unknown" or proposal.get("source") not in {None, "estimated"}:
            raise ContractError("automatic proposal semantics must remain unknown and estimated")
        masks.append(mask)
    return masks


def propose_instances(
    *, image_path: Path, store_path: Path, output_path: Path, backend: InstanceProposer
) -> dict[str, Any]:
    store = LocalArtifactStore(store_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    image = _import_image(store, image_path, "rgb_image")
    spec = load_default_operator_specs()["instance_proposals@1"]
    validate_operator_inputs(spec, {"image": image}, store)
    attempt = NodeAttempt(
        "propose_instances",
        1,
        "instance_proposals@1",
        "sam_v1",
        "running",
        "executed",
        utc_now(),
        None,
        None,
    )
    run = BuildRun(
        f"run_{uuid.uuid4().hex}",
        "instance_proposals",
        "1",
        "running",
        {"image": image},
        [attempt],
        utc_now(),
        None,
    )
    _persist_build_run(store, run)
    try:
        value = backend.propose(store, image)
        if (value.kind, value.schema_name, value.schema_version) != (
            "instance_proposals",
            "InstanceProposals",
            "1.0",
        ):
            raise ContractError("invalid instance proposals result contract")
        masks = _validate_result(store, image, value)
        enriched = []
        for proposal, mask in zip(value.value["proposals"], masks, strict=True):
            mask_provenance = _persist_provenance(
                store,
                run_id=run.run_id,
                node_id=attempt.node_id,
                port_name="masks",
                element_id=proposal["proposal_id"],
                artifact=mask,
                derived_from=[image],
                operator="instance_proposals",
                backend="sam_v1",
                backend_version="1",
                parameters=value.value["backend_metadata"],
                seed=None,
                source="estimated",
                model_digest=value.value["backend_metadata"]["checkpoint_digest"],
            )
            enriched.append({**proposal, "provenance": to_primitive(mask_provenance)})
        value = StructuredValue(
            value.kind,
            value.schema_name,
            value.schema_version,
            {**value.value, "proposals": enriched},
        )
        ref = store.persist_structured(value)
        provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id=attempt.node_id,
            port_name="proposals",
            artifact=ref,
            derived_from=[image],
            operator="instance_proposals",
            backend="sam_v1",
            backend_version="1",
            parameters=value.value["backend_metadata"],
            seed=None,
            source="estimated",
            model_digest=value.value["backend_metadata"]["checkpoint_digest"],
        )
        attempt.outputs = {"proposals": ref, "provenance": provenance}
        validate_operator_outputs(spec, attempt.outputs, store)
        attempt.status = "succeeded"
        attempt.finished_at = utc_now()
        attempt = NodeAttempt(
            "publish_proposals",
            1,
            "proposal_materialization@1",
            "core",
            "running",
            "executed",
            utc_now(),
            None,
            None,
        )
        run.node_attempts.append(attempt)
        _persist_build_run(store, run)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{output_path.name}-", dir=output_path.parent
        ) as temp:
            stage = Path(temp)
            for name, artifact in {
                "proposals.json": ref,
                "provenance.json": provenance,
                "image.input": image,
            }.items():
                (stage / name).write_bytes(store.blob_path(artifact).read_bytes())
            (stage / "proposals-ref.json").write_bytes(canonical_json_bytes(to_primitive(ref)))
            for p in value.value["proposals"]:
                directory = stage / "masks"
                directory.mkdir(exist_ok=True)
                (directory / f"{p['proposal_id']}.png").write_bytes(
                    store.blob_path(ArtifactRef(**p["mask"])).read_bytes()
                )
            attempt.status = "succeeded"
            attempt.finished_at = utc_now()
            run.status = "succeeded"
            run.finished_at = attempt.finished_at
            run_ref = _persist_build_run(store, run)
            (stage / "run.json").write_bytes(store.blob_path(run_ref).read_bytes())
            stage.rename(output_path)
        return {
            "proposals": to_primitive(ref),
            "run_id": run.run_id,
            "output_directory": str(output_path),
        }
    except Exception:
        attempt.status = "failed"
        attempt.error_code = (
            "release_failed" if attempt.node_id == "publish_proposals" else "backend_failed"
        )
        attempt.finished_at = utc_now()
        run.status = "failed"
        run.finished_at = attempt.finished_at
        _persist_build_run(store, run)
        raise


def select_instance_proposals(
    *,
    proposals: ArtifactRef,
    proposal_ids: list[str],
    reviewer: str,
    store_path: Path,
    output_path: Path,
    invert: bool = False,
    keep_largest: bool = False,
) -> dict[str, Any]:
    store = LocalArtifactStore(store_path)
    _checked(store, proposals, "instance_proposals")
    if not isinstance(invert, bool) or (invert and len(proposal_ids) != 1):
        raise ContractError("inversion requires exactly one selected proposal")
    if not isinstance(keep_largest, bool) or (keep_largest and len(proposal_ids) != 1):
        raise ContractError("largest component requires exactly one selected proposal")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ContractError("selection requires reviewer")
    raw = store.read_structured(proposals)
    indexed = {p["proposal_id"]: p for p in raw["proposals"]}
    if (
        not proposal_ids
        or len(set(proposal_ids)) != len(proposal_ids)
        or any(i not in indexed for i in proposal_ids)
    ):
        raise ContractError("select unique existing proposal IDs")
    if output_path.exists():
        raise FileExistsError(output_path)
    image = ArtifactRef(**raw["image"])
    _checked(store, image, "rgb_image")
    attempt = NodeAttempt(
        "select_instances",
        1,
        "instance_selection@1",
        "core",
        "running",
        "executed",
        utc_now(),
        None,
        None,
    )
    run = BuildRun(
        f"run_{uuid.uuid4().hex}",
        "instance_selection",
        "1",
        "running",
        {"proposals": proposals},
        [attempt],
        utc_now(),
        None,
    )
    _persist_build_run(store, run)
    try:
        if invert or keep_largest:
            original_proposals = proposals
            original = ArtifactRef(**indexed[proposal_ids[0]]["mask"])
            _checked(store, original, "binary_mask")
            validate_binary_mask(store, image, original)
            with Image.open(store.blob_path(original)) as source_mask:
                pixels = np.asarray(source_mask.convert("L"))
            if invert:
                pixels = 255 - pixels
            parameters: dict[str, Any] = {"operation": "invert_binary_mask"}
            if keep_largest:
                labels, count = ndimage.label(pixels != 0, structure=np.ones((3, 3)))
                sizes = np.bincount(labels.ravel())
                sizes[0] = 0
                winner = int(np.argmax(sizes)) if count else 0
                before = int(np.count_nonzero(pixels))
                pixels = np.asarray((labels == winner) & (labels != 0), dtype=np.uint8) * 255
                parameters = {
                    "operation": "keep_largest_component",
                    "invert_first": invert,
                    "connectivity": 8,
                    "tie_break": "first_row_major",
                    "policy_version": "1",
                    "removed_pixels": before - int(np.count_nonzero(pixels)),
                }
            if not np.any(pixels):
                raise ContractError("inverted mask has no foreground")
            with tempfile.TemporaryDirectory(prefix="inverted-mask-") as directory:
                path = Path(directory) / "mask.png"
                Image.fromarray(pixels).save(path)
                mask = _import_image(store, path, "binary_mask")
            mask_provenance = _persist_provenance(
                store,
                run_id=run.run_id,
                node_id=attempt.node_id,
                port_name="inverted_mask",
                artifact=mask,
                derived_from=[original, original_proposals],
                operator="instance_selection",
                backend="core",
                backend_version="1",
                parameters=parameters,
                seed=None,
                source="user",
            )
            ys, xs = np.nonzero(pixels)
            replacement = {
                **indexed[proposal_ids[0]],
                "mask": to_primitive(mask),
                "area": int(np.count_nonzero(pixels)),
                "bbox": [
                    int(xs.min()),
                    int(ys.min()),
                    int(xs.max() - xs.min() + 1),
                    int(ys.max() - ys.min() + 1),
                ],
                "predicted_iou": None,
                "stability_score": None,
                "source": "user",
                "provenance": to_primitive(mask_provenance),
            }
            raw = {
                **raw,
                "proposals": [
                    replacement if p["proposal_id"] == proposal_ids[0] else p
                    for p in raw["proposals"]
                ],
                "transformation": {
                    **parameters,
                    "source_proposals": to_primitive(original_proposals),
                    "proposal_id": proposal_ids[0],
                },
            }
            proposals = store.persist_structured(
                StructuredValue("instance_proposals", "InstanceProposals", "1.0", raw)
            )
            derived_provenance = _persist_provenance(
                store,
                run_id=run.run_id,
                node_id=attempt.node_id,
                port_name="derived_proposals",
                artifact=proposals,
                derived_from=[original_proposals, mask],
                operator="instance_selection",
                backend="core",
                backend_version="1",
                parameters=parameters,
                seed=None,
                source="user",
            )
            attempt.outputs = {
                "inverted_mask": mask,
                "mask_provenance": mask_provenance,
                "derived_proposals": proposals,
                "derived_provenance": derived_provenance,
            }
            indexed = {p["proposal_id"]: p for p in raw["proposals"]}
        selection = store.persist_structured(
            StructuredValue(
                "instance_selection",
                "InstanceSelection",
                "1.0",
                {
                    "proposals": to_primitive(proposals),
                    "selected_ids": proposal_ids,
                    "unselected_ids": sorted(set(indexed) - set(proposal_ids)),
                    "reviewer": reviewer.strip(),
                    "reviewed_at": utc_now(),
                    "scope": "mask_selection_only",
                    "semantic_labels": "unknown",
                },
            )
        )
        selected_masks = [ArtifactRef(**indexed[identifier]["mask"]) for identifier in proposal_ids]
        provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id=attempt.node_id,
            port_name="selection",
            artifact=selection,
            derived_from=[proposals, *selected_masks],
            operator="instance_selection",
            backend="core",
            backend_version="1",
            parameters={"selected_ids": proposal_ids, "reviewer": reviewer.strip()},
            seed=None,
            source="user",
        )
        attempt.outputs.update({"selection": selection, "provenance": provenance})
        attempt.status = "succeeded"
        attempt.finished_at = utc_now()
        attempt = NodeAttempt(
            "publish_selection",
            1,
            "selection_materialization@1",
            "core",
            "running",
            "executed",
            utc_now(),
            None,
            None,
        )
        run.node_attempts.append(attempt)
        _persist_build_run(store, run)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{output_path.name}-", dir=output_path.parent
        ) as temp:
            stage = Path(temp)
            (stage / "image.input").write_bytes(store.blob_path(image).read_bytes())
            objects = []
            for index, identifier in enumerate(proposal_ids):
                mask = ArtifactRef(**indexed[identifier]["mask"])
                _checked(store, mask, "binary_mask")
                name = f"object_{index + 1:03d}"
                filename = f"{name}.png"
                (stage / filename).write_bytes(store.blob_path(mask).read_bytes())
                objects.append({"object_id": name, "mask": filename})
            (stage / "objects.json").write_bytes(
                canonical_json_bytes(
                    {
                        "schema_version": "1.0",
                        "image": "image.input",
                        "objects": objects,
                        "instance_selection": to_primitive(selection),
                    }
                )
            )
            (stage / "selection.json").write_bytes(store.blob_path(selection).read_bytes())
            (stage / "selection-ref.json").write_bytes(
                canonical_json_bytes(to_primitive(selection))
            )
            (stage / "provenance.json").write_bytes(store.blob_path(provenance).read_bytes())
            attempt.status = "succeeded"
            attempt.finished_at = utc_now()
            run.status = "succeeded"
            run.finished_at = attempt.finished_at
            run_ref = _persist_build_run(store, run)
            (stage / "run.json").write_bytes(store.blob_path(run_ref).read_bytes())
            stage.rename(output_path)
        return {
            "selection": to_primitive(selection),
            "run_id": run.run_id,
            "manifest": str(output_path / "objects.json"),
        }
    except Exception:
        attempt.status = "failed"
        attempt.error_code = (
            "release_failed" if attempt.node_id == "publish_selection" else "selection_failed"
        )
        attempt.finished_at = utc_now()
        run.status = "failed"
        run.finished_at = attempt.finished_at
        _persist_build_run(store, run)
        raise

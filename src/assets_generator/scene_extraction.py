"""Generate independently reusable assets from explicitly supplied scene masks."""

from __future__ import annotations

import json
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifact_store import LocalArtifactStore
from .backend_registry import ResolvedPlan
from .completion import _checked
from .contracts import ContractError
from .errors import classify_error
from .models import ArtifactRef, BuildRun, NodeAttempt, StructuredValue
from .operators import validate_binary_mask
from .runtime import utc_now
from .serialization import canonical_json_bytes, to_primitive
from .workflow import _import_image, _persist_build_run, _persist_provenance, build_image_asset


@dataclass(frozen=True)
class SceneExtractionResult:
    manifest: ArtifactRef
    releases: dict[str, ArtifactRef]
    run_id: str
    output_directory: Path


def extract_scene_objects(
    *,
    manifest_path: Path,
    store_path: Path,
    output_path: Path,
    resolved_plan: ResolvedPlan,
    seed: int = 42,
    pipeline_type: str = "512",
) -> SceneExtractionResult:
    """Generate sequentially from one image and user masks; never infer placement.

    Relative input paths are resolved against the manifest directory. All masks
    are validated before generation. Child BuildRuns survive batch publication
    failure in the shared Artifact Store.
    """
    raw = json.loads(manifest_path.read_text())
    if not isinstance(raw, dict) or set(raw) not in (
        {"schema_version", "image", "objects"},
        {"schema_version", "image", "objects", "instance_selection"},
    ):
        raise ContractError("scene extraction requires schema_version, image and objects")
    if raw["schema_version"] != "1.0":
        raise ContractError("unsupported scene extraction schema_version")
    objects = raw["objects"]
    if not isinstance(objects, list) or not objects:
        raise ContractError("scene extraction requires nonempty objects")
    identifiers: set[str] = set()
    for obj in objects:
        if not isinstance(obj, dict) or set(obj) != {"object_id", "mask"}:
            raise ContractError("each object requires object_id and mask")
        identifier = obj["object_id"]
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", identifier)
            or identifier in identifiers
        ):
            raise ContractError("object_id must be unique and a safe simple identifier")
        identifiers.add(identifier)
    output = output_path.expanduser().absolute()
    if output.exists():
        raise FileExistsError(output)
    store = LocalArtifactStore(store_path)

    def import_input(path: Any, kind: str) -> ArtifactRef:
        if not isinstance(path, str) or not path:
            raise ContractError("image and mask paths must be nonempty strings")
        source = Path(path).expanduser()
        if not source.is_absolute():
            source = manifest_path.parent / source
        return _import_image(store, source, kind)

    image = import_input(raw["image"], "rgb_image")
    inputs = []
    for obj in objects:
        mask = import_input(obj["mask"], "binary_mask")
        validate_binary_mask(store, image, mask)
        inputs.append({"object_id": obj["object_id"], "mask": to_primitive(mask)})
    selection_evidence = None
    if "instance_selection" in raw:
        selection_evidence = ArtifactRef(**raw["instance_selection"])
        _checked(store, selection_evidence, "instance_selection")
        decision = store.read_structured(selection_evidence)
        proposal_ref = ArtifactRef(**decision["proposals"])
        _checked(store, proposal_ref, "instance_proposals")
        proposals = store.read_structured(proposal_ref)
        indexed = {p["proposal_id"]: p for p in proposals["proposals"]}
        expected_masks = [indexed[i]["mask"] for i in decision["selected_ids"]]
        if (
            proposals["image"] != to_primitive(image)
            or [obj["mask"] for obj in inputs] != expected_masks
        ):
            raise ContractError("instance selection does not match image and ordered masks")
    request = store.persist_structured(
        StructuredValue(
            "scene_extraction_request",
            "SceneExtractionRequest",
            "1.0",
            {
                "schema_version": "1.0",
                "image": to_primitive(image),
                "objects": inputs,
                "segmentation_source": "selected_model_proposals" if selection_evidence else "user",
                "instance_selection": to_primitive(selection_evidence)
                if selection_evidence
                else None,
                "seed": seed,
                "pipeline_type": pipeline_type,
                "resolved_plan_contract_digest": resolved_plan.contract_digest,
            },
        )
    )
    run = BuildRun(
        f"run_{uuid.uuid4().hex}",
        "scene_extraction",
        "1",
        "running",
        {"request": request},
        [],
        utc_now(),
        None,
    )
    _persist_build_run(store, run)
    results = []
    releases: dict[str, ArtifactRef] = {}
    attempt: NodeAttempt | None = None
    active_child_id: str | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temp:
            stage = Path(temp)
            for obj in inputs:
                identifier = obj["object_id"]
                attempt = NodeAttempt(
                    f"generate_{identifier}",
                    1,
                    "image_asset_v2",
                    None,
                    "running",
                    "executed",
                    utc_now(),
                    None,
                    None,
                )
                run.node_attempts.append(attempt)
                _persist_build_run(store, run)
                active_child_id = f"run_{uuid.uuid4().hex}"
                result = build_image_asset(
                    image_path=store.blob_path(image),
                    mask_path=store.blob_path(ArtifactRef(**obj["mask"])),
                    store_path=store_path,
                    output_path=stage / "objects" / identifier,
                    resolved_plan=resolved_plan,
                    seed=seed,
                    pipeline_type=pipeline_type,
                    asset_name=identifier,
                    run_id=active_child_id,
                )
                child_run = store.persist_structured(
                    StructuredValue(
                        "build_run", "BuildRun", "1.0", store.get_build_run(result.run_id)
                    )
                )
                attempt.outputs = {"release": result.release_manifest, "child_run": child_run}
                attempt.status = "succeeded"
                attempt.finished_at = utc_now()
                releases[identifier] = result.release_manifest
                results.append(
                    {
                        "object_id": identifier,
                        "release": to_primitive(result.release_manifest),
                        "asset_definition": to_primitive(result.asset_definition),
                        "run_id": result.run_id,
                        "directory": f"objects/{identifier}",
                        "mask": obj["mask"],
                        "source": "generated",
                    }
                )
                _persist_build_run(store, run)
                active_child_id = None
            attempt = NodeAttempt(
                "publish_extraction",
                1,
                "scene_extraction_publication@1",
                "core",
                "running",
                "executed",
                utc_now(),
                None,
                None,
            )
            run.node_attempts.append(attempt)
            _persist_build_run(store, run)
            reference = store.persist_structured(
                StructuredValue(
                    "scene_extraction",
                    "SceneExtraction",
                    "1.0",
                    {
                        "schema_version": "1.0",
                        "run_id": run.run_id,
                        "request": to_primitive(request),
                        "objects": results,
                        "policy": "provided-masks-independent-generation-v1",
                        "detection": "not_performed",
                        "pose_estimation": "not_performed",
                        "scene_assembly": "not_performed",
                    },
                )
            )
            provenance = _persist_provenance(
                store,
                run_id=run.run_id,
                node_id=attempt.node_id,
                port_name="manifest",
                artifact=reference,
                derived_from=[
                    request,
                    *([selection_evidence] if selection_evidence else []),
                    *releases.values(),
                ],
                operator="scene_extraction_publication",
                backend="core",
                backend_version="1",
                parameters={"policy": "provided-masks-independent-generation-v1"},
                seed=seed,
                source="derived",
            )
            attempt.outputs = {"manifest": reference, "provenance": provenance}
            for name, ref in {
                "extraction.json": reference,
                "request.json": request,
                "provenance.json": provenance,
            }.items():
                (stage / name).write_bytes(store.blob_path(ref).read_bytes())
            (stage / "extraction-ref.json").write_bytes(
                canonical_json_bytes(to_primitive(reference))
            )
            attempt.status = "succeeded"
            attempt.finished_at = utc_now()
            run.status = "succeeded"
            run.finished_at = attempt.finished_at
            run_ref = _persist_build_run(store, run)
            (stage / "run.json").write_bytes(store.blob_path(run_ref).read_bytes())
            stage.rename(output)
        return SceneExtractionResult(reference, releases, run.run_id, output)
    except Exception as error:
        if attempt is not None:
            if (
                active_child_id is not None
                and (store.root / "runs" / f"{active_child_id}.json").exists()
            ):
                attempt.outputs["child_run"] = store.persist_structured(
                    StructuredValue(
                        "build_run", "BuildRun", "1.0", store.get_build_run(active_child_id)
                    )
                )
            attempt.status = "failed"
            attempt.finished_at = utc_now()
            attempt.error_code = classify_error(error).value
        run.status = "failed"
        run.finished_at = utc_now()
        _persist_build_run(store, run)
        raise

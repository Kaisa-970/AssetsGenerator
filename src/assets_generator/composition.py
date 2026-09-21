"""Explicit face-centroid box selection and unfused component publication."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from .alignment import _glb, _mesh
from .artifact_store import LocalArtifactStore
from .completion import _checked
from .composition_release import standard_composition_release
from .contracts import ContractError, validate_operator_inputs, validate_operator_outputs
from .mesh_io import load_scene as _load_scene
from .models import ArtifactRef, BuildRun, NodeAttempt, StructuredValue
from .pipeline import load_default_operator_specs
from .provenance import persist_build_run as _persist_build_run
from .provenance import persist_provenance as _persist_provenance
from .runtime import utc_now
from .serialization import canonical_json_bytes, to_primitive


def records(store: LocalArtifactStore, kind: str) -> list[tuple[ArtifactRef, dict[str, Any]]]:
    result = []
    for path in store.manifests_dir.rglob("*.json"):
        import json

        raw = json.loads(path.read_text())
        if raw["identity"]["kind"] == kind:
            ref = ArtifactRef(raw["artifact_id"])
            _checked(store, ref, kind)
            result.append((ref, store.read_structured(ref)))
    return result


def selection_inputs(
    store: LocalArtifactStore, selection: ArtifactRef
) -> tuple[dict[str, Any], dict[str, Any]]:
    _checked(store, selection, "alignment_selection")
    selected = store.read_structured(selection)
    alignment = ArtifactRef(**selected["alignment"])
    _checked(store, alignment, "candidate_alignment")
    raw = store.read_structured(alignment)
    if store.get_build_run(raw["run_id"])["status"] != "succeeded":
        raise ContractError("alignment publication must have succeeded")
    reviews = [
        (ref, r)
        for ref, r in records(store, "alignment_review")
        if r["alignment"] == selected["alignment"]
    ]
    if {ref.artifact_id for ref, _ in reviews} != set(selected["review_ids"]):
        raise ContractError("review history changed; select alignment again")
    accepted = [r for ref, r in reviews if ref.artifact_id == selected["accepted_review_id"]]
    if not accepted or accepted[0]["decision"] != "accepted":
        raise ContractError("selection requires an accepted review")
    if (
        any(r["decision"] == "rejected" for _, r in reviews)
        and not selected["resolution_note"].strip()
    ):
        raise ContractError("conflicting reviews require explicit resolution")
    return selected, raw


def region_scene(
    store: LocalArtifactStore, selection: ArtifactRef, regions: dict[str, Any]
) -> tuple[trimesh.Scene, list[dict[str, Any]]]:
    _, raw = selection_inputs(store, selection)
    refs = {
        "reconstructed": _mesh(store, ArtifactRef(**raw["target_release"])),
        "generated": ArtifactRef(**raw["aligned"]),
    }
    if set(regions) != set(refs):
        raise ContractError("regions must declare reconstructed and generated")
    scene = trimesh.Scene()
    components = []
    for source, ref in refs.items():
        _checked(store, ref, "gltf_asset")
        rule = regions[source]
        mode = rule.get("mode")
        if mode not in {"all", "none", "inside", "outside"}:
            raise ContractError("region mode must be all/none/inside/outside")
        bounds = None
        if mode in {"inside", "outside"}:
            bounds = np.asarray([rule.get("minimum"), rule.get("maximum")], dtype=float)
            if (
                bounds.shape != (2, 3)
                or not np.isfinite(bounds).all()
                or np.any(bounds[0] >= bounds[1])
            ):
                raise ContractError("region box requires finite increasing minimum/maximum")
        original = _load_scene(store.blob_path(ref).read_bytes())
        for index, node in enumerate(sorted(original.graph.nodes_geometry)):
            pose, name = original.graph[node]
            mesh = original.geometry[name].copy()
            mesh.apply_transform(pose)
            centers = np.asarray(mesh.vertices)[np.asarray(mesh.faces)].mean(axis=1)
            keep = np.full(len(mesh.faces), mode != "none")
            if bounds is not None:
                keep = np.all((centers >= bounds[0]) & (centers <= bounds[1]), axis=1)
                if mode == "outside":
                    keep = ~keep
            face_ids = np.flatnonzero(keep).tolist()
            if not face_ids:
                continue
            # Preserve per-vertex UV/colors/material; no welding or topology repair.
            mesh.update_faces(keep)
            component_id = f"{source}_{index}"
            scene.add_geometry(mesh, node_name=component_id, geom_name=component_id)
            components.append(
                {
                    "component_id": component_id,
                    "source": source,
                    "input": to_primitive(ref),
                    "source_node": node,
                    "source_face_indices": face_ids,
                }
            )
    if not components:
        raise ContractError("region selection contains no faces")
    return scene, components


def publish_composition(
    *, store: LocalArtifactStore, selection: ArtifactRef, regions: dict[str, Any], output: Path
) -> dict[str, Any]:
    spec = load_default_operator_specs()["manual_region_composition@1"]
    validate_operator_inputs(spec, {"selection": selection}, store)
    selected, alignment = selection_inputs(store, selection)
    attempt = NodeAttempt(
        "compose_regions",
        1,
        "manual_region_composition@1",
        "core",
        "running",
        "executed",
        utc_now(),
        None,
        None,
    )
    run = BuildRun(
        f"run_{uuid.uuid4().hex}",
        "manual_region_composition",
        "1",
        "running",
        {"selection": selection},
        [attempt],
        utc_now(),
        None,
    )
    _persist_build_run(store, run)
    try:
        scene, components = region_scene(store, selection, regions)
        region_ref = store.persist_structured(
            StructuredValue(
                "region_selection",
                "RegionSelection",
                "1.0",
                {
                    "selection": to_primitive(selection),
                    "alignment": selected["alignment"],
                    "transform": alignment["transform"],
                    "rules": regions,
                    "policy": "target-glb-face-centroid-inclusive-box-v1",
                    "components": components,
                },
            )
        )
        mesh = store.persist_bytes(
            _glb(scene),
            kind="gltf_asset",
            schema_name="glTF",
            schema_version="2.0",
            identity_metadata={
                "frame_id": store.get_manifest(
                    ArtifactRef(**alignment["aligned"]).artifact_id
                ).identity.identity_metadata["frame_id"],
                "unit": alignment["target_unit"],
                "up_axis": "+Y",
                "forward_axis": "+Z",
                "region_selection": region_ref.artifact_id,
            },
        )
        provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id=attempt.node_id,
            port_name="mesh",
            artifact=mesh,
            derived_from=[
                selection,
                region_ref,
                ArtifactRef(**alignment["aligned"]),
                _mesh(store, ArtifactRef(**alignment["target_release"])),
            ],
            operator="manual_region_composition",
            backend="core",
            backend_version="1",
            parameters={"policy": "target-glb-face-centroid-inclusive-box-v1"},
            seed=None,
            source="mixed",
        )
        asset_ref, release_ref, quality_ref, release_files = standard_composition_release(
            store,
            scene=scene,
            components=components,
            selection=selection,
            selected=selected,
            alignment=alignment,
            regions=region_ref,
            mesh=mesh,
            provenance=provenance,
            run_id=run.run_id,
        )
        manifest = store.persist_structured(
            StructuredValue(
                "component_composition",
                "ComponentComposition",
                "1.0",
                {
                    "run_id": run.run_id,
                    "asset_definition": to_primitive(asset_ref),
                    "release": to_primitive(release_ref),
                    "quality_report": to_primitive(quality_ref),
                    "selection": to_primitive(selection),
                    "regions": to_primitive(region_ref),
                    "glb": to_primitive(mesh),
                    "provenance": to_primitive(provenance),
                    "status": "unfused_component_composition",
                    "welding": "not_performed",
                    "watertightness": "not_verified",
                },
            )
        )
        attempt.outputs = {
            "mesh": mesh,
            "asset": asset_ref,
            "release": release_ref,
            "quality": quality_ref,
            "exported": release_files["geometry/visual.glb"],
            "components": [
                ref
                for name, ref in release_files.items()
                if name.startswith("geometry/components/")
            ],
            "regions": region_ref,
            "manifest": manifest,
            "provenance": provenance,
        }
        validate_operator_outputs(spec, attempt.outputs, store)
        attempt.status = "succeeded"
        attempt.finished_at = utc_now()
        attempt = NodeAttempt(
            "publish_composition",
            1,
            "composition_materialization@1",
            "core",
            "running",
            "executed",
            utc_now(),
            None,
            None,
        )
        run.node_attempts.append(attempt)
        _persist_build_run(store, run)
        if output.exists():
            raise FileExistsError(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temp:
            stage = Path(temp)
            for name, ref in {
                **release_files,
                "asset.json": asset_ref,
                "release.json": release_ref,
                "visual.glb": mesh,
                "composition.json": manifest,
                "regions.json": region_ref,
                "provenance.json": provenance,
                "selection.json": selection,
            }.items():
                (stage / name).parent.mkdir(parents=True, exist_ok=True)
                (stage / name).write_bytes(store.blob_path(ref).read_bytes())
            (stage / "composition-ref.json").write_bytes(
                canonical_json_bytes(to_primitive(manifest))
            )
            attempt.status = "succeeded"
            attempt.finished_at = utc_now()
            run.status = "succeeded"
            run.finished_at = attempt.finished_at
            run_ref = _persist_build_run(store, run)
            (stage / "run.json").write_bytes(store.blob_path(run_ref).read_bytes())
            stage.rename(output)
        return {
            "composition": to_primitive(manifest),
            "release": to_primitive(release_ref),
            "output_directory": str(output),
        }
    except Exception:
        attempt.status = "failed"
        attempt.error_code = (
            "release_failed" if attempt.node_id == "publish_composition" else "output_invalid"
        )
        attempt.finished_at = utc_now()
        run.status = "failed"
        run.finished_at = attempt.finished_at
        _persist_build_run(store, run)
        raise

"""Explicit asset instancing into a scene; no automatic detection or pose estimation."""

from __future__ import annotations

import json
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from .alignment import _glb, _mesh, _similarity
from .artifact_store import LocalArtifactStore
from .completion import _checked
from .contracts import ContractError, validate_operator_inputs, validate_operator_outputs
from .mesh_io import load_scene as _load_scene
from .models import ArtifactRef, BuildRun, NodeAttempt, SpatialTransform, StructuredValue
from .pipeline import load_default_operator_specs
from .provenance import persist_build_run as _persist_build_run
from .provenance import persist_provenance as _persist_provenance
from .release_io import release_files as _release_files
from .runtime import utc_now
from .serialization import canonical_json_bytes, to_primitive


def build_scene(
    *, manifest_path: Path, store_path: Path, output_path: Path, run_id: str | None = None
) -> dict[str, Any]:
    """Publish supplied releases and explicit canonical-to-world poses as independent instances."""
    store = LocalArtifactStore(store_path)
    if run_id is None:
        run_id = f"run_{uuid.uuid4().hex}"
    elif not re.fullmatch(r"run_[A-Za-z0-9_-]+", run_id):
        raise ContractError("invalid explicit run_id")
    if (store.root / "runs" / f"{run_id}.json").exists():
        raise ContractError("explicit run_id already exists")
    raw = json.loads(manifest_path.read_text())
    if not isinstance(raw, dict):
        raise ContractError("scene manifest must be an object")
    if raw.get("schema_version") != "1.0" or raw.get("unit") not in {"meter", "relative_unit"}:
        raise ContractError("scene requires schema_version 1.0 and meter/relative_unit")
    frame = raw.get("frame_id")
    if not isinstance(frame, str) or not frame.strip():
        raise ContractError("scene requires a world frame_id")
    items = raw.get("instances")
    if not isinstance(items, list) or not items:
        raise ContractError("scene requires nonempty instances")
    if output_path.exists():
        raise FileExistsError(output_path)
    normalized = []
    release_files = {}
    names = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"instance_id", "release", "world_pose"}:
            raise ContractError("instance requires instance_id, release and world_pose")
        if (
            not isinstance(item["release"], dict)
            or set(item["release"]) != {"artifact_id"}
            or not isinstance(item["release"]["artifact_id"], str)
        ):
            raise ContractError("instance release must be an ArtifactRef")
        if not isinstance(item["world_pose"], dict) or set(item["world_pose"]) != {
            "source_frame_id",
            "target_frame_id",
            "matrix",
        }:
            raise ContractError("instance world_pose must be a SpatialTransform")
        name = item.get("instance_id")
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name)
            or name in names
        ):
            raise ContractError("instance_id must be unique and path-safe")
        names.add(name)
        release = ArtifactRef(**item["release"])
        mesh = _mesh(store, release)
        release_files[name] = _release_files(store, release)
        asset_ref = ArtifactRef(**store.read_structured(release)["asset_definition"])
        _checked(store, asset_ref, "asset_definition")
        asset = store.read_structured(asset_ref)
        spatial = asset["spatial"]
        meta = store.get_manifest(mesh.artifact_id).identity.identity_metadata
        if spatial["unit"] != raw["unit"] or meta["unit"] != raw["unit"]:
            raise ContractError(
                "scene unit must match each asset; implicit unit conversion forbidden"
            )
        transform = SpatialTransform(**item["world_pose"])
        expected = f"{asset_ref.artifact_id}/{spatial['canonical_frame_id']}"
        if transform.source_frame_id != expected or transform.target_frame_id != frame:
            raise ContractError(
                "world_pose requires scoped asset canonical source and scene target frame"
            )
        _similarity(transform)
        normalized.append(
            {
                "instance_id": name,
                "release": to_primitive(release),
                "asset_definition": to_primitive(asset_ref),
                "world_pose": to_primitive(transform),
                "pose_source": "user",
                "mesh": to_primitive(mesh),
            }
        )
    request = store.persist_structured(
        StructuredValue(
            "scene_request",
            "SceneRequest",
            "1.0",
            {
                "schema_version": "1.0",
                "frame_id": frame,
                "unit": raw["unit"],
                "instances": normalized,
            },
        )
    )
    spec = load_default_operator_specs()["assemble_scene@1"]
    validate_operator_inputs(spec, {"request": request}, store)
    attempt = NodeAttempt(
        "assemble_scene",
        1,
        "assemble_scene@1",
        "core",
        "running",
        "executed",
        utc_now(),
        None,
        None,
    )
    run = BuildRun(
        run_id,
        "scene_asset_v1",
        "1",
        "running",
        {"request": request},
        [attempt],
        utc_now(),
        None,
    )
    _persist_build_run(store, run)
    try:
        scene = trimesh.Scene()
        conversion = np.array([[0, 1, 0, 0], [0, 0, 1, 0], [1, 0, 0, 0], [0, 0, 0, 1]], dtype=float)
        instances = []
        evidence: dict[str, ArtifactRef] = {"evidence/request.json": request}
        for item in normalized:
            name = item["instance_id"]
            transform = SpatialTransform(**item["world_pose"])
            pose = conversion @ np.asarray(transform.matrix) @ conversion.T
            mesh = ArtifactRef(**item["mesh"])
            loaded = _load_scene(store.blob_path(mesh).read_bytes())
            if not loaded.geometry:
                raise ContractError("instance GLB has no geometry")
            for index, node in enumerate(sorted(loaded.graph.nodes_geometry)):
                local, geom = loaded.graph[node]
                geometry = loaded.geometry[geom].copy()
                if (
                    not len(geometry.faces)
                    or not np.isfinite(geometry.vertices).all()
                    or not np.isfinite(local).all()
                ):
                    raise ContractError("instance geometry must be nonempty and finite")
                scene.add_geometry(
                    geometry,
                    node_name=f"{name}_{index}",
                    geom_name=f"{name}_{index}",
                    transform=pose @ local,
                )
            transform_ref = store.persist_structured(
                StructuredValue("spatial_transform", "SpatialTransform", "1.0", item["world_pose"])
            )
            instance = {k: v for k, v in item.items() if k != "mesh"}
            instance["transform"] = to_primitive(transform_ref)
            instance_ref = store.persist_structured(
                StructuredValue("asset_instance", "AssetInstance", "1.0", instance)
            )
            provenance = _persist_provenance(
                store,
                run_id=run.run_id,
                node_id=attempt.node_id,
                port_name="instances",
                element_id=name,
                artifact=instance_ref,
                derived_from=[
                    request,
                    ArtifactRef(**item["release"]),
                    ArtifactRef(**item["asset_definition"]),
                    transform_ref,
                ],
                operator="assemble_scene",
                backend="core",
                backend_version="1",
                parameters={"output_element_id": name, "pose_source": "user"},
                seed=None,
                source="mixed",
            )
            instances.append(instance_ref)
            evidence[f"instances/{name}.json"] = instance_ref
            evidence[f"poses/{name}.json"] = transform_ref
            evidence[f"provenance/{name}.json"] = provenance
            evidence.update(
                {f"assets/{name}/{path}": ref for path, ref in release_files[name].items()}
            )
            evidence[f"assets/{name}/asset.json"] = ArtifactRef(**item["asset_definition"])
            evidence[f"assets/{name}/release.json"] = ArtifactRef(**item["release"])
        definition = store.persist_structured(
            StructuredValue(
                "scene_definition",
                "SceneDefinition",
                "1.0",
                {
                    "schema_version": "1.0",
                    "frame_id": frame,
                    "unit": raw["unit"],
                    "up_axis": "+Z",
                    "forward_axis": "+X",
                    "instances": [to_primitive(ref) for ref in instances],
                    "environment": None,
                    "relations": [],
                    "pose_status": "user_supplied_unverified",
                    "physics_status": "not_verified",
                },
            )
        )
        scene_provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id=attempt.node_id,
            port_name="scene",
            artifact=definition,
            derived_from=[request, *instances],
            operator="assemble_scene",
            backend="core",
            backend_version="1",
            parameters={"instance_count": len(instances), "pose_source": "user"},
            seed=None,
            source="mixed",
        )
        glb = store.persist_bytes(
            _glb(scene),
            kind="gltf_asset",
            schema_name="glTF",
            schema_version="2.0",
            identity_metadata={
                "frame_id": f"{definition.artifact_id}/gltf_export",
                "unit": raw["unit"],
                "up_axis": "+Y",
                "forward_axis": "+Z",
            },
        )
        glb_provenance = _persist_provenance(
            store,
            run_id=run.run_id,
            node_id=attempt.node_id,
            port_name="glb",
            artifact=glb,
            derived_from=[
                definition,
                *instances,
                *[ArtifactRef(**item["mesh"]) for item in normalized],
            ],
            operator="scene_export",
            backend="core",
            backend_version="1",
            parameters={"canonical_to_gltf": conversion.tolist()},
            seed=None,
            source="mixed",
        )
        evidence.update(
            {
                "scene.json": definition,
                "geometry/scene.glb": glb,
                "provenance/scene.json": scene_provenance,
                "provenance/export.json": glb_provenance,
            }
        )
        attempt.outputs = {
            "scene": definition,
            "instances": list(instances),
            "glb": glb,
            "scene_provenance": scene_provenance,
            "glb_provenance": glb_provenance,
        }
        validate_operator_outputs(spec, attempt.outputs, store)
        attempt.status = "succeeded"
        attempt.finished_at = utc_now()
        attempt = NodeAttempt(
            "publish_scene",
            1,
            "scene_materialization@1",
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
            for name, ref in evidence.items():
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(store.blob_path(ref).read_bytes())
            (stage / "scene-ref.json").write_bytes(canonical_json_bytes(to_primitive(definition)))
            attempt.status = "succeeded"
            attempt.finished_at = utc_now()
            run.status = "succeeded"
            run.finished_at = attempt.finished_at
            run_ref = _persist_build_run(store, run)
            (stage / "run.json").write_bytes(store.blob_path(run_ref).read_bytes())
            stage.rename(output_path)
        return {
            "scene": to_primitive(definition),
            "glb": to_primitive(glb),
            "run_id": run.run_id,
            "output_directory": str(output_path),
        }
    except Exception:
        attempt.status = "failed"
        attempt.error_code = (
            "release_failed" if attempt.node_id == "publish_scene" else "output_invalid"
        )
        attempt.finished_at = utc_now()
        run.status = "failed"
        run.finished_at = attempt.finished_at
        _persist_build_run(store, run)
        raise

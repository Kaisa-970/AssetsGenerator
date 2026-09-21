"""Explicit similarity alignment of independent candidates; no registration or fusion."""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from .artifact_store import LocalArtifactStore
from .completion import _checked
from .contracts import ContractError, validate_operator_inputs, validate_operator_outputs
from .errors import ErrorCode, classify_error
from .mesh_io import load_scene as _load_scene
from .models import ArtifactRef, BuildRun, NodeAttempt, SpatialTransform, StructuredValue
from .pipeline import load_default_operator_specs
from .provenance import persist_build_run as _persist_build_run
from .provenance import persist_provenance as _persist_provenance
from .runtime import utc_now
from .serialization import canonical_json_bytes, to_primitive


@dataclass(frozen=True)
class AlignmentResult:
    manifest: ArtifactRef
    aligned: ArtifactRef
    transform: ArtifactRef
    provenance: ArtifactRef
    output_directory: Path


def candidate_frame(release: ArtifactRef) -> str:
    """Scope export coordinates to a release, not a globally shared frame label."""
    return f"{release.artifact_id}/gltf_export"


def _similarity(transform: SpatialTransform) -> np.ndarray[Any, Any]:
    try:
        matrix = np.asarray(transform.matrix, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ContractError("alignment requires a finite 4x4 similarity matrix") from exc
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ContractError("alignment requires a finite 4x4 similarity matrix")
    linear = matrix[:3, :3]
    scale = float(np.linalg.norm(linear[:, 0]))
    if (
        not np.array_equal(matrix[3], [0, 0, 0, 1])
        or scale <= 0
        or not np.isfinite(scale)
        or not np.allclose((linear / scale).T @ (linear / scale), np.eye(3), atol=1e-8, rtol=0)
        or not np.isclose(np.linalg.det(linear / scale), 1, atol=1e-8, rtol=0)
    ):
        raise ContractError(
            "alignment requires rotation and positive uniform scale; no shear/reflection"
        )
    return matrix


def _mesh(store: LocalArtifactStore, release: ArtifactRef) -> ArtifactRef:
    _checked(store, release, "asset_release")
    raw = store.read_structured(release)
    if raw.get("export_profile") != "gltf2-v1":
        raise ContractError("alignment requires gltf2-v1 releases")
    ref = ArtifactRef(**raw["files"]["geometry/visual.glb"])
    _checked(store, ref, "gltf_asset")
    meta = store.get_manifest(ref.artifact_id).identity.identity_metadata
    if (meta.get("frame_id"), meta.get("up_axis"), meta.get("forward_axis")) != (
        "gltf_export",
        "+Y",
        "+Z",
    ) or meta.get("unit") not in {"meter", "relative_unit"}:
        raise ContractError("alignment requires declared GLTF export frame and supported unit")
    return ref


def _glb(scene: trimesh.Scene) -> bytes:
    data = scene.export(file_type="glb")  # type: ignore[no-untyped-call]
    if not isinstance(data, bytes):
        raise ContractError("alignment export did not produce GLB")
    return data


def align_completion_candidate(
    *,
    candidate: ArtifactRef,
    transform: SpatialTransform,
    store_path: Path,
    output_path: Path,
) -> AlignmentResult:
    """Apply T_reconstructed_generated in released GLB (+Y up) coordinates.

    The scale explicitly maps source coordinate units to target coordinate units.
    No metric accuracy or geometric correspondence is inferred.
    """
    # Reject non-serializable/non-similarity matrices before allocating a run.
    _similarity(transform)
    store = LocalArtifactStore(store_path)
    value = StructuredValue("spatial_transform", "SpatialTransform", "1.0", to_primitive(transform))
    attempt = NodeAttempt(
        "align_candidate",
        1,
        "align_candidate@1",
        "core",
        "running",
        "executed",
        utc_now(),
        None,
        None,
    )
    run = BuildRun(
        f"run_{uuid.uuid4().hex}",
        "candidate_alignment",
        "1",
        "running",
        {"candidate": candidate, "transform": value},
        [attempt],
        utc_now(),
        None,
    )
    _persist_build_run(store, run)
    try:
        return _execute_alignment(
            candidate=candidate, transform=transform, store=store, output_path=output_path, run=run
        )
    except Exception as error:
        active = run.node_attempts[-1]
        active.status = "failed"
        active.error_code = (
            ErrorCode.RELEASE_FAILED.value
            if active.node_id == "materialize_alignment"
            else classify_error(error).value
        )
        active.finished_at = utc_now()
        run.status = "failed"
        run.finished_at = active.finished_at
        _persist_build_run(store, run)
        raise


def _execute_alignment(
    *,
    candidate: ArtifactRef,
    transform: SpatialTransform,
    store: LocalArtifactStore,
    output_path: Path,
    run: BuildRun,
) -> AlignmentResult:
    spec = load_default_operator_specs()["align_candidate@1"]
    value = StructuredValue("spatial_transform", "SpatialTransform", "1.0", to_primitive(transform))
    validate_operator_inputs(spec, {"candidate": candidate, "transform": value}, store)
    raw = store.read_structured(candidate)
    if (
        raw.get("policy") != "independent-generation-candidate-v1"
        or raw.get("alignment") != "not_performed"
    ):
        raise ContractError("alignment requires an original independent generation candidate")
    source_release = ArtifactRef(**raw["generated"]["release"])
    target_release = ArtifactRef(**raw["reconstructed"]["release"])
    if (transform.source_frame_id, transform.target_frame_id) != (
        candidate_frame(source_release),
        candidate_frame(target_release),
    ):
        raise ContractError("transform source/target frame must match scoped candidate releases")
    matrix = _similarity(transform)
    source, target = _mesh(store, source_release), _mesh(store, target_release)
    source_meta = store.get_manifest(source.artifact_id).identity.identity_metadata
    target_meta = store.get_manifest(target.artifact_id).identity.identity_metadata
    output = output_path.expanduser().absolute()
    if output.exists():
        raise FileExistsError(output)
    scene = _load_scene(store.blob_path(source).read_bytes())
    scene.apply_transform(matrix)  # type: ignore[no-untyped-call]
    aligned_bytes = _glb(scene)
    transform_ref = store.persist_structured(value)
    aligned = store.persist_bytes(
        aligned_bytes,
        kind="gltf_asset",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={
            **source_meta,
            "frame_id": transform.target_frame_id,
            "unit": target_meta["unit"],
            "alignment_transform": transform_ref.artifact_id,
        },
    )
    provenance = _persist_provenance(
        store,
        run_id=run.run_id,
        node_id="align_candidate",
        port_name="aligned",
        artifact=aligned,
        derived_from=[candidate, source, target, transform_ref],
        operator="align_candidate",
        backend="core",
        backend_version="explicit-similarity-v1",
        parameters={
            "source_unit": source_meta["unit"],
            "target_unit": target_meta["unit"],
            "unit_mapping": "explicit_matrix_scale",
            "matrix_convention": "column_vector",
            "numpy_version": np.__version__,
            "trimesh_version": trimesh.__version__,
        },
        seed=None,
        source="generated",
    )
    validate_operator_outputs(spec, {"aligned": aligned, "transform": transform_ref}, store)
    manifest = {
        "schema_version": "1.0",
        "policy": "explicit-candidate-alignment-v1",
        "run_id": run.run_id,
        "candidate": to_primitive(candidate),
        "aligned": to_primitive(aligned),
        "transform": to_primitive(transform_ref),
        "provenance": to_primitive(provenance),
        "source_release": to_primitive(source_release),
        "target_release": to_primitive(target_release),
        "source_unit": source_meta["unit"],
        "target_unit": target_meta["unit"],
        "status": "aligned_candidate_only",
        "alignment": "explicit_transform_applied",
        "fusion": "not_performed",
        "review_status": "pending",
        "geometry_conditioning": False,
        "observed_surface_preservation": "not_guaranteed",
        "preview": {
            "file": "overlay.glb",
            "reconstructed": "cyan",
            "generated": "orange",
            "purpose": "diagnostic_only_not_fused",
        },
    }
    ref = store.persist_structured(
        StructuredValue("candidate_alignment", "CandidateAlignment", "1.0", manifest)
    )
    attempt = run.node_attempts[0]
    attempt.outputs = {"aligned": aligned, "transform": transform_ref}
    attempt.status = "succeeded"
    attempt.finished_at = utc_now()
    publication = NodeAttempt(
        "materialize_alignment",
        1,
        "alignment_materialization@1",
        "core",
        "running",
        "executed",
        utc_now(),
        None,
        None,
        {"manifest": ref, "provenance": provenance},
    )
    run.node_attempts.append(publication)
    _persist_build_run(store, run)
    # Diagnostic colors are applied only to copies; aligned.glb retains original visuals.
    overlay = trimesh.Scene()
    for label, layer, color in (
        ("reconstructed", _load_scene(store.blob_path(target).read_bytes()), [0, 190, 230, 255]),
        ("generated", scene.copy(), [255, 145, 40, 255]),
    ):
        for index, node in enumerate(layer.graph.nodes_geometry):
            pose, geometry_name = layer.graph[node]
            mesh = layer.geometry[geometry_name].copy()
            mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, vertex_colors=color)
            overlay.add_geometry(
                mesh, node_name=f"{label}_{index}", geom_name=f"{label}_{index}", transform=pose
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temp:
        stage = Path(temp)
        for name, artifact in {
            "alignment.json": ref,
            "transform.json": transform_ref,
            "provenance.json": provenance,
            "aligned.glb": aligned,
            "reconstructed.glb": target,
            "generated-original.glb": source,
        }.items():
            (stage / name).write_bytes(store.blob_path(artifact).read_bytes())
        (stage / "alignment-ref.json").write_bytes(canonical_json_bytes(to_primitive(ref)))
        (stage / "overlay.glb").write_bytes(_glb(overlay))
        panels = "".join(
            f'<div class="model-panel"><button data-model="{name}.glb" '
            f'data-label="{label}">查看模型</button>{label}'
            '<div class="model-status"></div></div>'
            for name, label in [
                ("overlay", "叠加：青色重建 / 橙色生成"),
                ("aligned", "已变换候选（原材质）"),
                ("reconstructed", "重建（原材质）"),
            ]
        )
        viewer = files("assets_generator.resources").joinpath("review-viewer.html").read_text()
        (stage / "index.html").write_text(
            '<!doctype html><meta charset="utf-8"><title>候选对齐预览</title>'
            "<h1>显式变换已应用 · 未融合 · 待检查</h1>"
            "<p>此预览不代表已正确配准；矩阵使用 GLB 坐标（+Y 向上）。</p>" + panels + viewer,
            encoding="utf-8",
        )
        publication.status = "succeeded"
        publication.finished_at = utc_now()
        run.status = "succeeded"
        run.finished_at = publication.finished_at
        run_ref = _persist_build_run(store, run)
        (stage / "run.json").write_bytes(store.blob_path(run_ref).read_bytes())
        stage.rename(output)
    return AlignmentResult(ref, aligned, transform_ref, provenance, output)

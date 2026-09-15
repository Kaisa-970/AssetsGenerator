from __future__ import annotations

import io
import json
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import trimesh
from PIL import Image

from .artifact_store import ArtifactStoreError, LocalArtifactStore
from .models import (
    SCHEMA_VERSION,
    AppearanceSet,
    ArtifactRef,
    AssetDefinition,
    AssetRelease,
    BackendNativeFrame,
    GeometrySet,
    PBRMaterial,
    QualityCheck,
    QualityReport,
    SemanticInfo,
    StructuredValue,
)
from .serialization import to_primitive
from .spatial import CanonicalizationResult, canonicalize_vertices


class OperatorExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedObservation:
    rgba: ArtifactRef
    bundle: StructuredValue
    image_warp: StructuredValue


@dataclass(frozen=True)
class ShapeOutput:
    mesh: ArtifactRef
    material: StructuredValue
    native_frame: StructuredValue
    backend_metadata: dict[str, Any]


@dataclass(frozen=True)
class CanonicalOutput:
    mesh: ArtifactRef
    frame: StructuredValue
    transform: StructuredValue
    spatial_info: StructuredValue
    result: CanonicalizationResult


def prepare_observation(
    store: LocalArtifactStore,
    image_ref: ArtifactRef,
    mask_ref: ArtifactRef,
) -> PreparedObservation:
    image = Image.open(store.blob_path(image_ref)).convert("RGB")
    mask = Image.open(store.blob_path(mask_ref)).convert("L")
    if image.size != mask.size:
        raise OperatorExecutionError(
            f"image and mask dimensions differ: {image.size} != {mask.size}"
        )
    mask_array = np.asarray(mask, dtype=np.uint8)
    alpha = np.where(mask_array > 127, 255, 0).astype(np.uint8)
    rgba_array = np.dstack((np.asarray(image, dtype=np.uint8), alpha))
    output = io.BytesIO()
    Image.fromarray(rgba_array, mode="RGBA").save(output, format="PNG", optimize=False)
    rgba_ref = store.persist_bytes(
        output.getvalue(),
        kind="rgba_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGBA"},
    )
    observation_id = f"observation_{uuid.uuid4().hex}"
    bundle = StructuredValue(
        "observation_bundle",
        "ObservationBundle",
        SCHEMA_VERSION,
        {
            "observation_id": observation_id,
            "views": [
                {
                    "view_id": "view_000",
                    "image": to_primitive(image_ref),
                    "mask": to_primitive(mask_ref),
                    "depth": None,
                    "camera": None,
                }
            ],
        },
    )
    warp = StructuredValue(
        "image_warp",
        "ImageWarp",
        SCHEMA_VERSION,
        {
            "source_view_id": "view_000",
            "target_view_id": "view_000_rgba",
            "crop": [0, 0, image.width, image.height],
            "resize": [image.width, image.height],
            "padding": [0, 0, 0, 0],
            "pixel_transform3x3": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        },
    )
    return PreparedObservation(rgba_ref, bundle, warp)


class Trellis2Backend:
    def __init__(
        self,
        python: Path,
        repo: Path,
        model: str = "microsoft/TRELLIS.2-4B",
        *,
        timeout_seconds: float = 1800.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.python = python.expanduser().absolute()
        self.repo = repo.expanduser().resolve()
        self.model = model
        self.timeout_seconds = timeout_seconds

    def _runner_path(self) -> Path:
        return (Path(__file__).parent / "backends" / "trellis2_runner.py").resolve()

    def generate(
        self,
        store: LocalArtifactStore,
        rgba: ArtifactRef,
        *,
        seed: int = 42,
        pipeline_type: str = "512",
        decimation_target: int = 300_000,
        texture_size: int = 2048,
    ) -> ShapeOutput:
        runner = self._runner_path()
        with tempfile.TemporaryDirectory(prefix="trellis2-", dir=store.root) as temporary:
            work = Path(temporary).resolve()
            request_path = work / "request.json"
            response_path = work / "response.json"
            output_path = work / "native.glb"
            request = {
                "repo": str(self.repo),
                "model": self.model,
                "input_image": str(store.blob_path(rgba).resolve()),
                "output_glb": str(output_path),
                "seed": seed,
                "pipeline_type": pipeline_type,
                "decimation_target": decimation_target,
                "texture_size": texture_size,
            }
            request_path.write_text(json.dumps(request), encoding="utf-8")
            try:
                completed = subprocess.run(
                    [str(self.python), str(runner), str(request_path), str(response_path)],
                    cwd=self.repo,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired as error:
                raise OperatorExecutionError(
                    f"TRELLIS.2 backend timed out after {self.timeout_seconds:g} seconds"
                ) from error
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip()
                raise OperatorExecutionError(f"TRELLIS.2 backend failed: {detail}")
            response = json.loads(response_path.read_text(encoding="utf-8"))
            mesh = store.persist_bytes(
                output_path.read_bytes(),
                kind="triangle_mesh",
                schema_name="glTF",
                schema_version="2.0",
                identity_metadata={
                    "media_type": "model/gltf-binary",
                    "frame_id": "trellis2_glb_native",
                    "unit": "relative_unit",
                    "up_axis": "+Y",
                    "forward_axis": None,
                },
            )
        native_frame = BackendNativeFrame(
            "trellis2_glb_native", "right", "+Y", None, "unknown", "relative_unit"
        )
        material = PBRMaterial([1.0, 1.0, 1.0, 1.0], alpha_mode="OPAQUE")
        return ShapeOutput(
            mesh,
            StructuredValue("pbr_material", "PBRMaterial", SCHEMA_VERSION, to_primitive(material)),
            StructuredValue(
                "backend_native_frame",
                "BackendNativeFrame",
                SCHEMA_VERSION,
                to_primitive(native_frame),
            ),
            response,
        )


def _load_scene(data: bytes) -> trimesh.Scene:
    loaded = trimesh.load(io.BytesIO(data), file_type="glb", force="scene")
    if not isinstance(loaded, trimesh.Scene):
        return trimesh.Scene(loaded)
    return loaded


def _scene_vertices(scene: trimesh.Scene) -> np.ndarray[Any, np.dtype[np.float64]]:
    vertices: list[np.ndarray[Any, np.dtype[np.float64]]] = []
    for node_name in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph[node_name]
        geometry = scene.geometry[geometry_name]
        points = trimesh.transform_points(np.asarray(geometry.vertices), transform)
        vertices.append(np.asarray(points, dtype=np.float64))
    if not vertices:
        raise OperatorExecutionError("GLB contains no mesh geometry")
    return np.vstack(vertices)


def canonicalize_glb(
    store: LocalArtifactStore,
    mesh: ArtifactRef,
    native_frame: BackendNativeFrame,
) -> CanonicalOutput:
    scene = _load_scene(store.blob_path(mesh).read_bytes())
    result = canonicalize_vertices(_scene_vertices(scene), native_frame)
    scene.apply_transform(np.asarray(result.transform.matrix, dtype=np.float64))
    exported = scene.export(file_type="glb")
    if not isinstance(exported, bytes):
        raise OperatorExecutionError("trimesh did not produce binary GLB output")
    mesh_ref = store.persist_bytes(
        exported,
        kind="triangle_mesh",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={
            "media_type": "model/gltf-binary",
            "frame_id": result.frame.frame_id,
            "unit": result.frame.unit,
            "up_axis": result.frame.up_axis,
            "forward_axis": result.frame.forward_axis,
            "forward_status": result.spatial_info.forward_status,
        },
    )
    return CanonicalOutput(
        mesh_ref,
        StructuredValue("frame", "Frame", SCHEMA_VERSION, to_primitive(result.frame)),
        StructuredValue(
            "spatial_transform", "SpatialTransform", SCHEMA_VERSION, to_primitive(result.transform)
        ),
        StructuredValue(
            "asset_spatial_info",
            "AssetSpatialInfo",
            SCHEMA_VERSION,
            to_primitive(result.spatial_info),
        ),
        result,
    )


def validate_geometry(
    store: LocalArtifactStore,
    mesh: ArtifactRef,
    *,
    derived_from: ArtifactRef,
    run_id: str | None = None,
) -> QualityReport:
    checks: list[QualityCheck] = []
    try:
        scene = _load_scene(store.blob_path(mesh).read_bytes())
        vertices = _scene_vertices(scene)
        faces = sum(len(geometry.faces) for geometry in scene.geometry.values())
        checks.append(
            QualityCheck("glb_loadable", True, len(scene.geometry), "geometry-v1", "pass")
        )
        status: Literal["pass", "fail"] = (
            "pass" if len(vertices) > 0 and faces > 0 and np.isfinite(vertices).all() else "fail"
        )
        checks.append(QualityCheck("mesh_non_empty_finite", True, faces, "geometry-v1", status))
    except Exception as error:
        checks.append(QualityCheck("glb_loadable", True, None, "geometry-v1", "fail", str(error)))
    checks.append(
        QualityCheck(
            "blob_digest",
            True,
            None,
            "geometry-v1",
            "pass" if store.verify_digest(mesh) else "fail",
        )
    )
    try:
        metadata = store.get_manifest(mesh.artifact_id).identity.identity_metadata
    except (ArtifactStoreError, FileNotFoundError, KeyError, TypeError, ValueError):
        metadata = {}
    spatial_ok = bool(metadata.get("frame_id") and metadata.get("unit"))
    checks.append(
        QualityCheck(
            "spatial_contract", True, None, "geometry-v1", "pass" if spatial_ok else "fail"
        )
    )
    provenance_matches: list[ArtifactRef] = []
    for reference in store.find_artifacts("provenance_record"):
        if not store.verify_digest(reference):
            continue
        try:
            record = store.read_structured(reference)
        except (ArtifactStoreError, FileNotFoundError, KeyError, TypeError, ValueError):
            continue
        required_fields = {
            "provenance_id",
            "output_id",
            "output_artifact_id",
            "derived_from_artifact_ids",
            "operator",
            "operator_version",
            "backend",
            "backend_version",
            "parameters",
            "run_id",
            "node_id",
            "attempt",
            "source",
        }
        if (
            required_fields <= record.keys()
            and record["output_artifact_id"] == mesh.artifact_id
            and derived_from.artifact_id in record["derived_from_artifact_ids"]
            and record["operator"] == "canonicalize"
            and record["node_id"] == "canonicalize"
            and (run_id is None or record["run_id"] == run_id)
        ):
            provenance_matches.append(reference)
    checks.append(
        QualityCheck(
            "mandatory_provenance",
            True,
            len(provenance_matches),
            "geometry-v1",
            "pass" if provenance_matches else "fail",
            None if provenance_matches else "no valid provenance derives the canonical mesh",
            provenance_matches,
        )
    )
    scale_status = "metric" if metadata.get("unit") == "meter" else "relative"
    checks.append(
        QualityCheck(
            "metric_scale",
            True,
            scale_status,
            "geometry-v1",
            "pass" if scale_status == "metric" else "warn",
            None if scale_status == "metric" else "asset uses relative scale",
        )
    )
    forward_status = str(metadata.get("forward_status", "unknown"))
    checks.append(
        QualityCheck(
            "deterministic_forward",
            True,
            forward_status,
            "geometry-v1",
            "pass" if forward_status == "declared" else "warn",
            None
            if forward_status == "declared"
            else "forward direction was mechanically estimated",
        )
    )
    checks.append(
        QualityCheck(
            "collision_loadable",
            False,
            None,
            "geometry-v1",
            "skipped",
            "collision output was not requested",
        )
    )
    checks.append(
        QualityCheck(
            "render_back",
            False,
            None,
            "geometry-v1",
            "skipped",
            "camera registration is not implemented in Phase 1",
        )
    )
    statuses = {check.status for check in checks}
    overall: Literal["pass", "warn", "fail"] = (
        "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
    )
    return QualityReport("geometry-v1", checks, overall)


def assemble_asset(
    mesh: ArtifactRef,
    material: PBRMaterial,
    spatial: Any,
    observation_id: str,
    quality_report: ArtifactRef,
    semantics: SemanticInfo | None = None,
    name: str | None = None,
) -> AssetDefinition:
    asset_id = f"asset_{uuid.uuid4().hex}"
    return AssetDefinition(
        asset_id,
        "1.0",
        name,
        GeometrySet([mesh]),
        AppearanceSet([material]),
        spatial,
        semantics or SemanticInfo(None, "unknown"),
        None,
        [observation_id],
        [quality_report.artifact_id],
    )


def export_release(
    store: LocalArtifactStore,
    asset_ref: ArtifactRef,
    canonical_mesh: ArtifactRef,
    quality_ref: ArtifactRef,
) -> tuple[ArtifactRef, AssetRelease]:
    scene = _load_scene(store.blob_path(canonical_mesh).read_bytes())
    canonical_to_gltf = np.array(
        [[0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    scene.apply_transform(canonical_to_gltf)
    exported = scene.export(file_type="glb")
    if not isinstance(exported, bytes):
        raise OperatorExecutionError("trimesh did not produce binary GLB output")
    glb_ref = store.persist_bytes(
        exported,
        kind="gltf_asset",
        schema_name="glTF",
        schema_version="2.0",
        identity_metadata={
            "media_type": "model/gltf-binary",
            "frame_id": "gltf_export",
            "unit": store.get_manifest(canonical_mesh.artifact_id).identity.identity_metadata[
                "unit"
            ],
            "up_axis": "+Y",
            "forward_axis": "+Z",
            "export_profile": "gltf2-v1",
        },
    )
    release = AssetRelease(
        asset_ref,
        {"geometry/visual.glb": glb_ref, "qa/quality-report.json": quality_ref},
        "gltf2-v1",
    )
    return glb_ref, release

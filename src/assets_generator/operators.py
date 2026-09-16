from __future__ import annotations

import io
import json
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import trimesh
from PIL import Image

from .artifact_store import ArtifactStoreError, LocalArtifactStore
from .backends.model_identity import snapshot_digest, snapshot_state
from .errors import ErrorCode, PipelineError
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
from .serialization import cache_key, to_primitive
from .spatial import CanonicalizationResult, canonicalize_vertices
from .worker import LocalProcessWorker, ProcessJobRequest


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
    cache_hit: bool = False


@dataclass(frozen=True)
class SegmentationOutput:
    mask: ArtifactRef
    result: StructuredValue
    backend_metadata: dict[str, Any]
    cache_hit: bool = False


@dataclass(frozen=True)
class CanonicalOutput:
    mesh: ArtifactRef
    frame: StructuredValue
    transform: StructuredValue
    spatial_info: StructuredValue
    result: CanonicalizationResult


def validate_binary_mask(
    store: LocalArtifactStore, image_ref: ArtifactRef, mask_ref: ArtifactRef
) -> None:
    try:
        with Image.open(store.blob_path(image_ref)) as image:
            image_size = image.size
        with Image.open(store.blob_path(mask_ref)) as mask:
            values = np.asarray(mask.convert("L"), dtype=np.uint8)
            mask_size = mask.size
    except (ArtifactStoreError, OSError) as error:
        raise PipelineError(
            ErrorCode.OUTPUT_INVALID, f"invalid segmentation mask: {error}"
        ) from error
    if mask_size != image_size:
        raise PipelineError(
            ErrorCode.OUTPUT_INVALID,
            f"segmentation mask dimensions differ: {mask_size} != {image_size}",
        )
    if not np.isin(values, [0, 255]).all():
        raise PipelineError(
            ErrorCode.OUTPUT_INVALID,
            "segmentation mask contains values other than 0 and 255",
        )
    if not np.any(values == 255):
        raise PipelineError(
            ErrorCode.OUTPUT_INVALID,
            "segmentation mask contains no foreground pixels",
        )


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
        worker: LocalProcessWorker | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.python = python.expanduser().absolute()
        self.repo = repo.expanduser().resolve()
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.worker = worker or LocalProcessWorker()

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
            job = self.worker.run(
                ProcessJobRequest(
                    [str(self.python), str(runner), str(request_path), str(response_path)],
                    self.repo,
                    self.timeout_seconds,
                    request_path.resolve().as_uri(),
                )
            )
            response = json.loads(response_path.read_text(encoding="utf-8"))
            response["worker_job_id"] = job.job_id
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


class TripoSRBackend:
    def __init__(
        self,
        python: Path,
        repo: Path,
        model: str | Path = "stabilityai/TripoSR",
        *,
        timeout_seconds: float = 900.0,
        chunk_size: int = 8192,
        mc_resolution: int = 256,
        foreground_ratio: float = 0.85,
        worker: LocalProcessWorker | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if mc_resolution <= 0:
            raise ValueError("mc_resolution must be positive")
        if not 0 < foreground_ratio <= 1:
            raise ValueError("foreground_ratio must be in (0, 1]")
        self.python = python.expanduser().absolute()
        self.repo = repo.expanduser().resolve()
        if isinstance(model, Path):
            self.model = str(model.expanduser().resolve())
        else:
            local_model = Path(model).expanduser()
            self.model = str(local_model.resolve()) if local_model.is_dir() else model
        self.timeout_seconds = timeout_seconds
        self.chunk_size = chunk_size
        self.mc_resolution = mc_resolution
        self.foreground_ratio = foreground_ratio
        self.worker = worker or LocalProcessWorker()

    def _runner_path(self) -> Path:
        return (Path(__file__).parent / "backends" / "triposr_runner.py").resolve()

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
        del decimation_target, texture_size
        with tempfile.TemporaryDirectory(prefix="triposr-", dir=store.root) as temporary:
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
                "chunk_size": self.chunk_size,
                "mc_resolution": self.mc_resolution,
                "foreground_ratio": self.foreground_ratio,
            }
            request_path.write_text(json.dumps(request), encoding="utf-8")
            job = self.worker.run(
                ProcessJobRequest(
                    [
                        str(self.python),
                        str(self._runner_path()),
                        str(request_path),
                        str(response_path),
                    ],
                    self.repo,
                    self.timeout_seconds,
                    request_path.resolve().as_uri(),
                )
            )
            if not response_path.is_file() or not output_path.is_file():
                raise PipelineError(
                    ErrorCode.OUTPUT_INVALID,
                    "TripoSR backend did not produce its declared outputs",
                )
            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise PipelineError(
                    ErrorCode.OUTPUT_INVALID, f"invalid TripoSR response: {error}"
                ) from error
            self._validate_response(response, request)
            try:
                glb = output_path.read_bytes()
                scene = _load_scene(glb)
                vertices = _scene_vertices(scene)
                face_count = sum(len(geometry.faces) for geometry in scene.geometry.values())
            except (OSError, ValueError, TypeError, OperatorExecutionError) as error:
                raise PipelineError(
                    ErrorCode.OUTPUT_INVALID, f"invalid TripoSR GLB: {error}"
                ) from error
            if face_count <= 0 or not np.isfinite(vertices).all():
                raise PipelineError(ErrorCode.OUTPUT_INVALID, "TripoSR GLB has invalid geometry")
            response["worker_job_id"] = job.job_id
            response["validated_vertex_count"] = int(len(vertices))
            response["validated_face_count"] = int(face_count)
            response["native_frame_validation"] = "triposr-z-up-export-reload-v1"
            mesh = store.persist_bytes(
                glb,
                kind="triangle_mesh",
                schema_name="glTF",
                schema_version="2.0",
                identity_metadata={
                    "media_type": "model/gltf-binary",
                    "frame_id": "triposr_glb_native",
                    "unit": "relative_unit",
                    "up_axis": "+Z",
                    "forward_axis": None,
                },
            )
        native_frame = BackendNativeFrame(
            "triposr_glb_native", "right", "+Z", None, "unknown", "relative_unit"
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

    @staticmethod
    def _validate_response(response: Any, request: dict[str, Any]) -> None:
        if not isinstance(response, dict):
            raise PipelineError(ErrorCode.OUTPUT_INVALID, "TripoSR response must be an object")
        required = {
            "backend",
            "backend_version",
            "model",
            "model_digest",
            "seed",
            "seed_effective",
            "pipeline_type",
            "pipeline_type_effective",
            "chunk_size",
            "mc_resolution",
            "foreground_ratio",
            "vertex_count",
            "face_count",
            "peak_cuda_memory_mb",
        }
        missing = sorted(required - response.keys())
        if missing:
            raise PipelineError(
                ErrorCode.OUTPUT_INVALID,
                f"TripoSR response missing fields: {', '.join(missing)}",
            )
        if response["backend"] != "triposr":
            raise PipelineError(ErrorCode.OUTPUT_INVALID, "unexpected TripoSR backend identity")
        if not isinstance(response["backend_version"], str) or not response["backend_version"]:
            raise PipelineError(ErrorCode.OUTPUT_INVALID, "missing TripoSR backend version")
        digest = response["model_digest"]
        if not isinstance(digest, str) or len(digest) != 71 or not digest.startswith("sha256:"):
            raise PipelineError(ErrorCode.OUTPUT_INVALID, "missing TripoSR model digest")
        try:
            int(digest.removeprefix("sha256:"), 16)
        except ValueError as error:
            raise PipelineError(ErrorCode.OUTPUT_INVALID, "invalid TripoSR model digest") from error
        for field in ("model", "seed", "pipeline_type", "chunk_size", "mc_resolution"):
            if response[field] != request[field]:
                raise PipelineError(
                    ErrorCode.OUTPUT_INVALID, f"TripoSR response changed request field: {field}"
                )
        if response["seed_effective"] is not False:
            raise PipelineError(
                ErrorCode.OUTPUT_INVALID, "TripoSR seed must be declared ineffective"
            )
        if response["pipeline_type_effective"] is not False:
            raise PipelineError(
                ErrorCode.OUTPUT_INVALID, "TripoSR pipeline_type must be declared ineffective"
            )
        if not isinstance(response["foreground_ratio"], (int, float)) or not np.isclose(
            response["foreground_ratio"], request["foreground_ratio"]
        ):
            raise PipelineError(
                ErrorCode.OUTPUT_INVALID, "TripoSR response changed foreground_ratio"
            )
        for field in ("vertex_count", "face_count"):
            if not isinstance(response[field], int) or response[field] <= 0:
                raise PipelineError(ErrorCode.OUTPUT_INVALID, f"invalid TripoSR {field}")
        peak = response["peak_cuda_memory_mb"]
        if not isinstance(peak, (int, float)) or peak < 0:
            raise PipelineError(ErrorCode.OUTPUT_INVALID, "invalid TripoSR peak_cuda_memory_mb")


class BiRefNetSegmentationBackend:
    MODEL_ID = "ZhengPeng7/BiRefNet_lite"
    MODEL_REVISION = "aa62cd87eafb9cc43056d08ef3615a14628b831d"

    def __init__(
        self,
        python: Path,
        *,
        threshold: int = 128,
        timeout_seconds: float = 300.0,
        worker: LocalProcessWorker | None = None,
    ) -> None:
        if not 0 <= threshold <= 255:
            raise ValueError("threshold must be between 0 and 255")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.python = python.expanduser().absolute()
        self.threshold = threshold
        self.timeout_seconds = timeout_seconds
        self.worker = worker or LocalProcessWorker()
        self._cached_model_identity: str | None = None
        self._identity_binding: tuple[str, str, str, str] | None = None
        self._snapshot_path: Path | None = None
        self._snapshot_state: list[list[str | int]] | None = None

    def _runner_path(self) -> Path:
        return (Path(__file__).parent / "backends" / "birefnet_runner.py").resolve()

    def _model_cache_identity(self) -> str:
        binding = (self.MODEL_ID, self.MODEL_REVISION, str(self.python), str(self._runner_path()))
        if self._snapshot_path is not None and self._identity_binding == binding:
            try:
                unchanged = snapshot_state(self._snapshot_path) == self._snapshot_state
            except (OSError, ValueError):
                unchanged = False
            if unchanged and self._cached_model_identity is not None:
                # File timestamps can be coarse or preserved during weight replacement.
                if snapshot_digest(self._snapshot_path) == self._cached_model_identity:
                    return self._cached_model_identity
        with tempfile.TemporaryDirectory(prefix="birefnet-identity-") as temporary:
            work = Path(temporary)
            request = work / "request.json"
            response = work / "response.json"
            request.write_text(
                json.dumps(
                    {
                        "action": "identity",
                        "model": self.MODEL_ID,
                        "revision": self.MODEL_REVISION,
                    }
                ),
                encoding="utf-8",
            )
            self.worker.run(
                ProcessJobRequest(
                    [str(self.python), str(self._runner_path()), str(request), str(response)],
                    Path.cwd(),
                    self.timeout_seconds,
                    uuid.uuid4().hex,
                )
            )
            identity_response = json.loads(response.read_text(encoding="utf-8"))
            digest = identity_response.get("model_digest")
            if not isinstance(digest, str) or not digest.startswith("sha256:"):
                raise PipelineError(ErrorCode.OUTPUT_INVALID, "missing model digest")
            self._cached_model_identity = digest
            snapshot = identity_response.get("snapshot_path")
            self._snapshot_path = Path(snapshot) if snapshot else None
            self._snapshot_state = identity_response.get("snapshot_state")
            self._identity_binding = binding
            return digest

    def segment(self, store: LocalArtifactStore, image: ArtifactRef) -> SegmentationOutput:
        model_identity = self._model_cache_identity()
        key_payload = {
            "operator": "segmentation@1",
            "backend": "birefnet_lite",
            "model_revision": self.MODEL_REVISION,
            "threshold": self.threshold,
            "input_artifact_id": image.artifact_id,
            "model_identity": model_identity,
        }
        key = cache_key(key_payload)
        cached = store.get_cache(key)
        if cached is not None:
            mask = ArtifactRef(str(cached["mask_artifact_id"]))
            validate_binary_mask(store, image, mask)
            metadata = dict(cached["backend_metadata"])
            return SegmentationOutput(
                mask,
                StructuredValue(
                    "segmentation_result",
                    "SegmentationResult",
                    SCHEMA_VERSION,
                    {"source": "cache", "threshold": self.threshold},
                ),
                metadata,
                True,
            )

        with tempfile.TemporaryDirectory(prefix="birefnet-", dir=store.root) as temporary:
            work = Path(temporary).resolve()
            request_path = work / "request.json"
            response_path = work / "response.json"
            output_path = work / "mask.png"
            request = {
                "model": self.MODEL_ID,
                "revision": self.MODEL_REVISION,
                "input_image": str(store.blob_path(image).resolve()),
                "output_mask": str(output_path),
                "threshold": self.threshold,
                "expected_model_digest": model_identity,
            }
            request_path.write_text(json.dumps(request), encoding="utf-8")
            job = self.worker.run(
                ProcessJobRequest(
                    [
                        str(self.python),
                        str(self._runner_path()),
                        str(request_path),
                        str(response_path),
                    ],
                    Path.cwd(),
                    self.timeout_seconds,
                    uuid.uuid4().hex,
                )
            )
            if not response_path.is_file() or not output_path.is_file():
                raise PipelineError(
                    ErrorCode.OUTPUT_INVALID,
                    "BiRefNet backend did not produce its declared outputs",
                )
            response = json.loads(response_path.read_text(encoding="utf-8"))
            if response.get("model_digest") != model_identity:
                raise PipelineError(
                    ErrorCode.OUTPUT_INVALID, "model digest changed during execution"
                )
            response["worker_job_id"] = job.job_id
            mask = store.persist_bytes(
                output_path.read_bytes(),
                kind="binary_mask",
                schema_name="png",
                schema_version="1.0",
                identity_metadata={
                    "media_type": "image/png",
                    "channel_layout": "L",
                    "threshold": self.threshold,
                },
            )
            validate_binary_mask(store, image, mask)
        if response.get("model_digest"):
            store.put_cache(
                key,
                {
                    "artifact_ids": [mask.artifact_id],
                    "mask_artifact_id": mask.artifact_id,
                    "backend_metadata": response,
                },
            )
        return SegmentationOutput(
            mask,
            StructuredValue(
                "segmentation_result",
                "SegmentationResult",
                SCHEMA_VERSION,
                {"source": "generated", "threshold": self.threshold},
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


def material_from_glb(store: LocalArtifactStore, mesh: ArtifactRef) -> PBRMaterial:
    scene = _load_scene(store.blob_path(mesh).read_bytes())
    for geometry in scene.geometry.values():
        material = getattr(geometry.visual, "material", None)
        color = getattr(material, "baseColorFactor", None) if material is not None else None
        if color is not None:
            values = [float(v) / 255.0 if float(v) > 1 else float(v) for v in color]
            return PBRMaterial((values + [1.0])[:4])
    return PBRMaterial([1.0, 1.0, 1.0, 1.0])


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

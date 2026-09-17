from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from PIL import Image

from ..artifact_store import LocalArtifactStore
from ..contracts import ContractError
from ..models import SCHEMA_VERSION, ArtifactRef, StructuredValue
from ..observations import (
    camera_record_from_mapping,
    observation_bundle_from_artifact,
    validate_camera_record,
)
from ..operators import GeometryFrontendOutput
from ..serialization import sha256_bytes, to_primitive
from ..worker import LocalProcessWorker, ProcessJobRequest
from .model_identity import snapshot_digest, snapshot_state
from .source_identity import backend_source_identity


class DA3GeometryFrontend:
    """RGB-only DA3 frontend, isolated from Core in a dedicated Python process."""

    def __init__(
        self,
        python: Path,
        repo: Path,
        model: Path,
        *,
        process_res: int = 392,
        timeout_seconds: float = 1800.0,
        worker: LocalProcessWorker | None = None,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if isinstance(process_res, bool) or not isinstance(process_res, int) or process_res < 14:
            raise ValueError("process_res must be an integer of at least 14")
        self.python = python.expanduser().absolute()
        self.repo = repo.expanduser().resolve()
        self.model = model.expanduser().resolve()
        self.process_res = process_res
        self.timeout_seconds = timeout_seconds
        self.worker = worker or LocalProcessWorker()

    def _runner_path(self) -> Path:
        return Path(__file__).with_name("da3_runner.py")

    def estimate(
        self, store: LocalArtifactStore, observations: ArtifactRef
    ) -> GeometryFrontendOutput:
        bundle = observation_bundle_from_artifact(observations, store)
        if not self.model.is_dir():
            raise ContractError("DA3 requires a local model snapshot directory")
        source = backend_source_identity(self.repo)
        model_state = snapshot_state(self.model)
        model_digest = snapshot_digest(self.model)
        runner = self._runner_path()
        runner_digest = sha256_bytes(runner.read_bytes())
        with tempfile.TemporaryDirectory(prefix="da3-", dir=store.root) as temporary:
            work = Path(temporary).resolve()
            request_path, response_path = work / "request.json", work / "response.json"
            request = {
                "repo": str(self.repo),
                "model": str(self.model),
                "observations": [
                    {"view_id": view.view_id, "image": str(store.blob_path(view.image).resolve())}
                    for view in bundle.views
                ],
                "output_dir": str(work),
                "process_res": self.process_res,
            }
            request_path.write_text(json.dumps(request), encoding="utf-8")
            job = self.worker.run(
                ProcessJobRequest(
                    [str(self.python), str(runner), str(request_path), str(response_path)],
                    self.repo,
                    self.timeout_seconds,
                    request_path.as_uri(),
                )
            )
            if (
                backend_source_identity(self.repo) != source
                or snapshot_state(self.model) != model_state
                or sha256_bytes(runner.read_bytes()) != runner_digest
            ):
                raise ContractError("DA3 source or model changed during inference")
            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
                cameras, depths, points, metadata = self._validate_response(
                    response, work, bundle.views, store
                )
            except (OSError, ValueError, TypeError, KeyError) as error:
                raise ContractError(f"invalid DA3 response: {error}") from error
            depth_refs = [
                store.persist_bytes(
                    data,
                    kind="depth_map",
                    schema_name="tiff",
                    schema_version="1.0",
                    identity_metadata={
                        "media_type": "image/tiff",
                        "view_id": view.view_id,
                        "frame_id": f"da3_camera_{index}",
                        "unit": "relative_unit",
                        "invalid_value": 0.0,
                    },
                )
                for index, (view, data) in enumerate(zip(bundle.views, depths, strict=True))
            ]
            point_ref = store.persist_bytes(
                points,
                kind="point_cloud",
                schema_name="ply",
                schema_version="1.0",
                identity_metadata={
                    "media_type": "application/ply",
                    "frame_id": "da3_world",
                    "unit": "relative_unit",
                },
            )
        parameters: dict[str, Any] = {
            key: metadata[key]
            for key in (
                "process_res_method",
                "preprocessing",
                "depth_resampling",
                "invalid_value",
                "confidence_percentile",
                "confidence_threshold",
                "point_sampling_stride",
                "unit",
                "world_frame",
                "software_versions",
                "python_version",
                "cuda_version",
            )
            if key in metadata
        }
        parameters.update(
            process_res=self.process_res,
            input_mode="rgb_only",
            runner_digest=runner_digest,
        )
        metadata.update(
            backend_version=f"da3:{source['revision']}",
            backend_source=source,
            model_digest=model_digest,
            worker_job_id=job.job_id,
            parameters=parameters,
        )
        return GeometryFrontendOutput(cameras, depth_refs, point_ref, metadata)

    @staticmethod
    def _validate_response(
        response: Any, work: Path, views: Any, store: LocalArtifactStore
    ) -> tuple[list[StructuredValue], list[bytes], bytes, dict[str, Any]]:
        if not isinstance(response, dict):
            raise ContractError("DA3 response must be an object")
        raw_cameras, raw_depths = response.get("cameras"), response.get("depths")
        if (
            not isinstance(raw_cameras, list)
            or not isinstance(raw_depths, list)
            or len(raw_cameras) != len(views)
            or len(raw_depths) != len(views)
        ):
            raise ContractError("DA3 must return one camera and depth per input view")

        def output_path(raw: object) -> Path:
            if not isinstance(raw, str):
                raise ContractError("DA3 output path must be a string")
            path = Path(raw)
            path = (work / path).resolve() if not path.is_absolute() else path.resolve()
            if not path.is_relative_to(work) or not path.is_file():
                raise ContractError("DA3 output must be a file inside its work directory")
            return path

        cameras, depths = [], []
        camera_ids: set[str] = set()
        for index, (raw_camera, raw_depth, view) in enumerate(
            zip(raw_cameras, raw_depths, views, strict=True)
        ):
            camera = camera_record_from_mapping(raw_camera)
            validate_camera_record(camera, view)
            if (
                camera.source != "estimated"
                or camera.camera_frame_id != f"da3_camera_{index}"
                or camera.T_world_camera is None
                or camera.T_world_camera.target_frame_id != "da3_world"
                or camera.camera_id in camera_ids
            ):
                raise ContractError("DA3 camera violates estimated camera/world frame contract")
            camera_ids.add(camera.camera_id)
            with Image.open(store.blob_path(view.image)) as image:
                size = image.size
            if size != (camera.width, camera.height):
                raise ContractError("DA3 camera dimensions must match original image")
            if not isinstance(raw_depth, dict) or raw_depth.get("view_id") != view.view_id:
                raise ContractError("DA3 depth must match ordered input view_id")
            path = output_path(raw_depth.get("path"))
            with Image.open(path) as depth:
                if depth.format != "TIFF" or depth.mode != "F" or depth.size != size:
                    raise ContractError("DA3 depth must be float TIFF at original image size")
                values = np.asarray(depth)
                if not np.isfinite(values).all() or (values < 0).any() or not (values > 0).any():
                    raise ContractError(
                        "DA3 depth must contain finite nonnegative values and valid depth"
                    )
            depths.append(path.read_bytes())
            cameras.append(
                StructuredValue(
                    "camera_record", "CameraRecord", SCHEMA_VERSION, to_primitive(camera)
                )
            )
        points_path = output_path(response.get("points"))
        try:
            points = trimesh.load(points_path, file_type="ply", process=False)
        except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
            raise ContractError(f"invalid DA3 response: unreadable PLY: {error}") from error
        if (
            not isinstance(points, trimesh.points.PointCloud)
            or len(points.vertices) == 0
            or not np.isfinite(points.vertices).all()
        ):
            raise ContractError("DA3 points must be a nonempty finite PLY point cloud")
        metadata = response.get("backend_metadata", {})
        if not isinstance(metadata, dict):
            raise ContractError("DA3 backend_metadata must be an object")
        return cameras, depths, points_path.read_bytes(), dict(metadata)

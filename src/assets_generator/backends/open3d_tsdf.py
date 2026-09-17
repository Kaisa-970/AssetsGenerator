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
from ..models import (
    SCHEMA_VERSION,
    ArtifactRef,
    BackendNativeFrame,
    ComponentProvenance,
    PBRMaterial,
    StructuredValue,
)
from ..observations import (
    camera_record_from_mapping,
    observation_bundle_from_artifact,
    validate_camera_record,
)
from ..operators import ReconstructionOutput
from ..serialization import sha256_bytes, to_primitive
from ..worker import LocalProcessWorker, ProcessJobRequest
from .environment_identity import open3d_environment_identity


class Open3DReconstruction:
    """Fuse calibrated RGB/depth observations in an isolated Open3D process."""

    def __init__(
        self,
        python: Path,
        *,
        voxel_size_ratio: float = 0.01,
        sdf_trunc_ratio: float = 0.04,
        depth_trunc_ratio: float = 3.0,
        up_axis: str = "-Y",
        timeout_seconds: float = 1800.0,
        worker: LocalProcessWorker | None = None,
    ) -> None:
        for name, value in (
            ("voxel_size_ratio", voxel_size_ratio),
            ("sdf_trunc_ratio", sdf_trunc_ratio),
            ("depth_trunc_ratio", depth_trunc_ratio),
            ("timeout_seconds", timeout_seconds),
        ):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if sdf_trunc_ratio < voxel_size_ratio:
            raise ValueError("sdf_trunc_ratio must be at least voxel_size_ratio")
        if up_axis not in {"+X", "-X", "+Y", "-Y", "+Z", "-Z"}:
            raise ValueError("up_axis must be a signed coordinate axis")
        self.python = python.expanduser().absolute()
        self.voxel_size_ratio = voxel_size_ratio
        self.sdf_trunc_ratio = sdf_trunc_ratio
        self.depth_trunc_ratio = depth_trunc_ratio
        self.up_axis = up_axis
        self.timeout_seconds = timeout_seconds
        self.worker = worker or LocalProcessWorker()

    def _runner_path(self) -> Path:
        return Path(__file__).with_name("open3d_runner.py")

    def reconstruct(
        self,
        store: LocalArtifactStore,
        observations: ArtifactRef,
        cameras: list[StructuredValue],
        depths: list[ArtifactRef],
        points: ArtifactRef,
    ) -> ReconstructionOutput:
        rows, world_frame, unit = self._inputs(store, observations, cameras, depths, points)
        runner = self._runner_path()
        runner_digest = sha256_bytes(runner.read_bytes())
        try:
            environment = open3d_environment_identity(self.python)
        except ValueError as error:
            raise ContractError(str(error)) from error
        parameters: dict[str, Any] = {
            "voxel_size_ratio": self.voxel_size_ratio,
            "sdf_trunc_ratio": self.sdf_trunc_ratio,
            "depth_trunc_ratio": self.depth_trunc_ratio,
            "runner_digest": runner_digest,
            "up_axis": self.up_axis,
            "up_axis_source": "configured_convention_not_gravity_estimation",
            "points_usage": "frame_and_unit_validation_only",
            "appearance": "vertex_colors",
        }
        with tempfile.TemporaryDirectory(prefix="open3d-", dir=store.root) as temporary:
            work = Path(temporary).resolve()
            request_path, response_path = work / "request.json", work / "response.json"
            request = {
                "views": rows,
                "output_dir": str(work),
                "world_frame": world_frame,
                "unit": unit,
                "voxel_size_ratio": self.voxel_size_ratio,
                "sdf_trunc_ratio": self.sdf_trunc_ratio,
                "depth_trunc_ratio": self.depth_trunc_ratio,
            }
            request_path.write_text(json.dumps(request), encoding="utf-8")
            job = self.worker.run(
                ProcessJobRequest(
                    [str(self.python), str(runner), str(request_path), str(response_path)],
                    work,
                    self.timeout_seconds,
                    request_path.as_uri(),
                )
            )
            if sha256_bytes(runner.read_bytes()) != runner_digest:
                raise ContractError("Open3D runner changed during reconstruction")
            try:
                current_environment = open3d_environment_identity(self.python)
            except ValueError as error:
                raise ContractError(str(error)) from error
            if current_environment != environment:
                raise ContractError("Open3D environment changed during reconstruction")
            try:
                mesh_bytes, metadata = self._response(
                    json.loads(response_path.read_text(encoding="utf-8")), work, world_frame, unit
                )
            except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
                raise ContractError(f"invalid Open3D response: {error}") from error
            mesh = store.persist_bytes(
                mesh_bytes,
                kind="triangle_mesh",
                schema_name="glTF",
                schema_version="2.0",
                identity_metadata={
                    "frame_id": world_frame,
                    "unit": unit,
                    "up_axis": self.up_axis,
                    "forward_axis": None,
                },
            )
        parameters = {
            **{
                key: metadata[key]
                for key in (
                    "scale_rule",
                    "reference_depth",
                    "voxel_length",
                    "sdf_trunc",
                    "depth_trunc",
                    "world_frame",
                    "unit",
                    "depth_scale",
                    "extrinsic_convention",
                    "color_type",
                    "software_versions",
                    "open3d_version",
                    "python_version",
                )
                if key in metadata
            },
            **parameters,
            "backend_environment": environment,
        }
        metadata.update(
            backend_version=f"open3d:{metadata['open3d_version']}",
            backend_environment=environment,
            worker_job_id=job.job_id,
            parameters=parameters,
        )
        frame = BackendNativeFrame(world_frame, "right", self.up_axis, None, "unknown", unit)
        return ReconstructionOutput(
            mesh,
            StructuredValue(
                "pbr_material",
                "PBRMaterial",
                SCHEMA_VERSION,
                to_primitive(PBRMaterial([1.0, 1.0, 1.0, 1.0])),
            ),
            StructuredValue(
                "backend_native_frame", "BackendNativeFrame", SCHEMA_VERSION, to_primitive(frame)
            ),
            [
                StructuredValue(
                    "component_provenance",
                    "ComponentProvenance",
                    SCHEMA_VERSION,
                    to_primitive(ComponentProvenance("body", mesh, "reconstructed")),
                )
            ],
            metadata,
        )

    @staticmethod
    def _inputs(
        store: LocalArtifactStore,
        observations: ArtifactRef,
        cameras: list[StructuredValue],
        depths: list[ArtifactRef],
        points: ArtifactRef,
    ) -> tuple[list[dict[str, Any]], str, str]:
        bundle = observation_bundle_from_artifact(observations, store)
        views = {view.view_id: view for view in bundle.views}
        if not store.verify_digest(points):
            raise ContractError("Open3D points have invalid artifact digest")
        identity = store.get_manifest(points.artifact_id).identity
        world_frame = identity.identity_metadata.get("frame_id")
        unit = identity.identity_metadata.get("unit")
        if (
            identity.kind != "point_cloud"
            or not isinstance(world_frame, str)
            or not world_frame
            or not isinstance(unit, str)
            or unit not in {"meter", "relative_unit"}
        ):
            raise ContractError("Open3D requires points with world frame and unit")
        by_view = {}
        camera_ids: set[str] = set()
        for value in cameras:
            if (value.kind, value.schema_name, value.schema_version) != (
                "camera_record",
                "CameraRecord",
                SCHEMA_VERSION,
            ):
                raise ContractError("Open3D requires CameraRecord structured values")
            camera = camera_record_from_mapping(value.value)
            view_id = camera.image_view_id
            if view_id not in views or view_id in by_view or camera.camera_id in camera_ids:
                raise ContractError("Open3D camera has duplicate or unknown view/camera identity")
            validate_camera_record(camera, views[view_id])
            if camera.model != "pinhole" or any(camera.distortion):
                raise ContractError("Open3D requires undistorted pinhole cameras")
            if (
                camera.T_world_camera is None
                or camera.T_world_camera.target_frame_id != world_frame
            ):
                raise ContractError("Open3D camera world frame must match points frame")
            with Image.open(store.blob_path(views[view_id].image)) as image:
                if image.size != (camera.width, camera.height):
                    raise ContractError("Open3D camera dimensions must match image")
            by_view[view_id] = camera
            camera_ids.add(camera.camera_id)
        by_depth = {}
        for depth in depths:
            if not store.verify_digest(depth):
                raise ContractError("Open3D depth has invalid artifact digest")
            identity = store.get_manifest(depth.artifact_id).identity
            metadata = identity.identity_metadata
            depth_view_id = metadata.get("view_id")
            if (
                not isinstance(depth_view_id, str)
                or depth_view_id not in views
                or depth_view_id in by_depth
            ):
                raise ContractError("Open3D depth has duplicate or unknown view")
            depth_camera = by_view.get(depth_view_id)
            if (
                identity.kind != "depth_map"
                or depth_camera is None
                or metadata.get("frame_id") != depth_camera.camera_frame_id
                or metadata.get("unit") != unit
            ):
                raise ContractError("Open3D depth frame/unit must match camera and points")
            invalid = metadata.get("invalid_value", 0.0)
            if (
                isinstance(invalid, bool)
                or not isinstance(invalid, (int, float))
                or not math.isfinite(invalid)
            ):
                raise ContractError("Open3D depth invalid_value must be finite numeric")
            with Image.open(store.blob_path(depth)) as image:
                values = np.asarray(image)
                if (
                    image.size != (depth_camera.width, depth_camera.height)
                    or values.ndim != 2
                    or not np.issubdtype(values.dtype, np.number)
                ):
                    raise ContractError("Open3D depth dimensions/encoding must match camera")
                valid = values != invalid
                if not np.isfinite(values[valid]).all() or (values[valid] < 0).any():
                    raise ContractError("Open3D depth must be finite and nonnegative")
                if not (valid & (values > 0)).any():
                    raise ContractError("Open3D depth must contain valid positive depth")
            by_depth[depth_view_id] = (depth, invalid)
        if set(by_view) != set(views) or set(by_depth) != set(views) or not views:
            raise ContractError("Open3D requires one camera and depth for every observation")
        rows = []
        for view in bundle.views:
            depth, invalid = by_depth[view.view_id]
            row = {
                "view_id": view.view_id,
                "image": str(store.blob_path(view.image).resolve()),
                "depth": str(store.blob_path(depth).resolve()),
                "intrinsics": [
                    [by_view[view.view_id].fx, 0.0, by_view[view.view_id].cx],
                    [0.0, by_view[view.view_id].fy, by_view[view.view_id].cy],
                    [0.0, 0.0, 1.0],
                ],
                "T_world_camera": to_primitive(by_view[view.view_id].T_world_camera)["matrix"],
                "mask": None,
                "invalid_value": invalid,
            }
            if view.mask is not None:
                row["mask"] = str(store.blob_path(view.mask).resolve())
            rows.append(row)
        return rows, world_frame, unit

    @staticmethod
    def _response(
        response: Any, work: Path, world_frame: str, unit: str
    ) -> tuple[bytes, dict[str, Any]]:
        if not isinstance(response, dict) or not isinstance(response.get("mesh"), str):
            raise ContractError("Open3D response requires a mesh path")
        path = Path(response["mesh"])
        path = (work / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(work) or not path.is_file():
            raise ContractError("Open3D mesh must be a file inside its work directory")
        scene = trimesh.load(path, file_type="glb", force="scene", process=False)
        if not isinstance(scene, trimesh.Scene) or not scene.geometry:
            raise ContractError("Open3D mesh must contain nonempty triangle geometry")
        for geometry in scene.geometry.values():
            if (
                not isinstance(geometry, trimesh.Trimesh)
                or not len(geometry.faces)
                or not len(geometry.vertices)
                or not np.isfinite(geometry.vertices).all()
                or geometry.faces.min() < 0
                or geometry.faces.max() >= len(geometry.vertices)
            ):
                raise ContractError("Open3D mesh must contain nonempty finite triangles")
            if geometry.visual is None or geometry.visual.kind != "vertex":
                raise ContractError("Open3D mesh must retain vertex colors")
        metadata = response.get("backend_metadata")
        if (
            not isinstance(metadata, dict)
            or not isinstance(metadata.get("open3d_version"), str)
            or not metadata["open3d_version"]
            or not isinstance(metadata.get("software_versions"), dict)
            or not metadata["software_versions"]
        ):
            raise ContractError("Open3D backend_metadata requires software versions")
        if metadata.get("world_frame") != world_frame or metadata.get("unit") != unit:
            raise ContractError("Open3D response frame/unit differs from input")
        return path.read_bytes(), dict(metadata)

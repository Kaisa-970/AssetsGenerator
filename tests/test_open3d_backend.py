from __future__ import annotations

import io
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.backends.open3d_tsdf import Open3DReconstruction
from assets_generator.contracts import ContractError
from assets_generator.errors import ErrorCode, PipelineError
from assets_generator.models import (
    SCHEMA_VERSION,
    CameraRecord,
    ObservationView,
    SpatialTransform,
    StructuredValue,
)
from assets_generator.observations import make_observation_bundle, observation_bundle_value
from assets_generator.serialization import to_primitive
from assets_generator.worker import WorkerJob


class StubWorker:
    def __init__(self, mutation=None):
        self.mutation = mutation
        self.requests = []
        self.payloads = []

    def run(self, job):
        self.requests.append(job)
        request = json.loads(Path(job.command[-2]).read_text())
        self.payloads.append(request)
        mesh = trimesh.creation.box()
        mesh.visual.vertex_colors = np.tile([15, 42, 87, 255], (len(mesh.vertices), 1))
        path = Path(request["output_dir"]) / "mesh.glb"
        path.write_bytes(trimesh.Scene(mesh).export(file_type="glb"))
        response = {
            "mesh": str(path),
            "backend_metadata": {
                "open3d_version": "0.19.0",
                "software_versions": {"open3d": "0.19.0"},
                "reference_depth": 2.0,
                "world_frame": request["world_frame"],
                "unit": request["unit"],
                "fusion_and_export_seconds": 1.234,
            },
        }
        if self.mutation:
            self.mutation(response, request, job)
        Path(job.command[-1]).write_text(json.dumps(response))
        return WorkerJob("test", job.idempotency_key, "succeeded", "now")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "assets_generator.backends.open3d_tsdf.open3d_environment_identity",
        lambda python: {"environment_digest": "sha256:" + "a" * 64},
    )
    store = LocalArtifactStore(tmp_path / "store")
    views, cameras, depths = [], [], []
    for index, view_id in enumerate(["left", "right"]):

        def raster(array, kind, fmt, metadata=None):
            buffer = io.BytesIO()
            Image.fromarray(array).save(buffer, format=fmt)
            return store.persist_bytes(
                buffer.getvalue(),
                kind=kind,
                schema_name=fmt.lower(),
                schema_version="1.0",
                identity_metadata=metadata or {},
            )

        image = raster(np.full((6, 8, 3), 100, np.uint8), "rgb_image", "PNG")
        mask = raster(np.full((6, 8), 255, np.uint8), "binary_mask", "PNG")
        views.append(ObservationView(view_id, image, mask))
        frame = f"camera_{index}"
        camera = CameraRecord(
            frame,
            view_id,
            "pinhole",
            8,
            6,
            10.0,
            10.0,
            4.0,
            3.0,
            [],
            frame,
            SpatialTransform(frame, "world", np.eye(4).tolist()),
            "estimated",
        )
        cameras.append(
            StructuredValue("camera_record", "CameraRecord", SCHEMA_VERSION, to_primitive(camera))
        )
        depths.append(
            raster(
                np.ones((6, 8), np.float32),
                "depth_map",
                "TIFF",
                {
                    "view_id": view_id,
                    "frame_id": frame,
                    "unit": "relative_unit",
                    "invalid_value": 0.0,
                },
            )
        )
    observation = store.persist_structured(
        observation_bundle_value(make_observation_bundle(views, store))
    )
    points = store.persist_bytes(
        trimesh.points.PointCloud([[0, 0, 1]]).export(file_type="ply"),
        kind="point_cloud",
        schema_name="ply",
        schema_version="1.0",
        identity_metadata={"frame_id": "world", "unit": "relative_unit"},
    )
    runner = tmp_path / "runner.py"
    runner.write_text("# runner")
    monkeypatch.setattr(Open3DReconstruction, "_runner_path", lambda self: runner)
    return store, observation, cameras, depths, points


def test_request_alignment_and_colored_artifact(setup, tmp_path):
    store, observation, cameras, depths, points = setup
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    worker = StubWorker()
    backend = Open3DReconstruction(python, worker=worker)
    result = backend.reconstruct(
        store, observation, list(reversed(cameras)), list(reversed(depths)), points
    )
    request = worker.payloads[0]
    assert worker.requests[0].command[0] == str(python)
    assert [view["view_id"] for view in request["views"]] == ["left", "right"]
    assert request["views"][0]["mask"] is not None
    assert request["views"][0]["intrinsics"] == [
        [10.0, 0.0, 4.0],
        [0.0, 10.0, 3.0],
        [0.0, 0.0, 1.0],
    ]
    assert request["views"][0]["T_world_camera"] == np.eye(4).tolist()
    assert request["voxel_size_ratio"] == 0.01
    assert request["world_frame"] == "world"
    assert not Path(worker.requests[0].command[-1]).exists()
    assert store.verify_digest(result.mesh)
    identity = store.get_manifest(result.mesh.artifact_id).identity.identity_metadata
    assert identity == {
        "frame_id": "world",
        "unit": "relative_unit",
        "up_axis": "-Y",
        "forward_axis": None,
    }
    scene = trimesh.load(store.blob_path(result.mesh), file_type="glb")
    assert all(mesh.visual.kind == "vertex" for mesh in scene.geometry.values())
    assert result.components[0].value["provenance_ids"] == []
    assert result.native_frame.value["forward_status"] == "unknown"
    assert result.backend_metadata["parameters"]["points_usage"] == "frame_and_unit_validation_only"
    assert result.backend_metadata["parameters"]["reference_depth"] == 2.0
    assert "fusion_and_export_seconds" not in result.backend_metadata["parameters"]
    assert result.backend_metadata["backend_version"] == "open3d:0.19.0"
    assert result.backend_metadata["backend_environment"]["environment_digest"].startswith(
        "sha256:"
    )
    assert (
        result.backend_metadata["parameters"]["backend_environment"]
        == result.backend_metadata["backend_environment"]
    )


@pytest.mark.parametrize("target", ["observation", "depth", "points", "image", "mask"])
def test_corrupt_input_rejected_before_worker(setup, target):
    store, observation, cameras, depths, points = setup
    from assets_generator.observations import observation_bundle_from_artifact

    view = observation_bundle_from_artifact(observation, store).views[0]
    refs = {
        "observation": observation,
        "depth": depths[0],
        "points": points,
        "image": view.image,
        "mask": view.mask,
    }
    store.blob_path(refs[target]).write_bytes(b"corrupt")
    worker = StubWorker()
    with pytest.raises(ContractError):
        Open3DReconstruction(Path(sys.executable), worker=worker).reconstruct(*setup)
    assert not worker.requests


@pytest.mark.parametrize(
    "case",
    [
        "missing_camera",
        "missing_depth",
        "duplicate_camera",
        "duplicate_depth",
        "unknown_camera",
        "distortion",
        "world_frame",
        "depth_frame",
        "depth_unit",
        "unknown_depth",
    ],
)
def test_incompatible_inputs_rejected(setup, case):
    store, observation, cameras, depths, points = setup
    if case == "missing_camera":
        cameras.pop()
    elif case == "missing_depth":
        depths.pop()
    elif case == "duplicate_camera":
        cameras[1] = cameras[0]
    elif case == "duplicate_depth":
        depths[1] = depths[0]
    elif case in {"unknown_camera", "distortion", "world_frame"}:
        raw = json.loads(json.dumps(cameras[0].value))
        if case == "unknown_camera":
            raw["image_view_id"] = "unknown"
        elif case == "distortion":
            raw["distortion"] = [0.1]
        else:
            raw["T_world_camera"]["target_frame_id"] = "other"
        cameras[0] = replace(cameras[0], value=raw)
    else:
        old = depths[0]
        metadata = dict(store.get_manifest(old.artifact_id).identity.identity_metadata)
        metadata[
            {"depth_frame": "frame_id", "depth_unit": "unit", "unknown_depth": "view_id"}[case]
        ] = "other"
        depths[0] = store.persist_bytes(
            store.blob_path(old).read_bytes(),
            kind="depth_map",
            schema_name="tiff",
            schema_version="1.0",
            identity_metadata=metadata,
        )
    worker = StubWorker()
    with pytest.raises(ContractError):
        Open3DReconstruction(Path(sys.executable), worker=worker).reconstruct(
            store, observation, cameras, depths, points
        )
    assert not worker.requests


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda r, q, j: r.update(mesh=q["views"][0]["image"]), "inside its work"),
        (lambda r, q, j: r.update(backend_metadata={}), "software versions"),
        (lambda r, q, j: Path(r["mesh"]).write_bytes(b"bad"), "invalid Open3D response"),
        (lambda r, q, j: Path(j.command[1]).write_text("# changed"), "runner changed"),
    ],
)
def test_invalid_response(setup, mutation, match):
    with pytest.raises(ContractError, match=match):
        Open3DReconstruction(Path(sys.executable), worker=StubWorker(mutation)).reconstruct(*setup)


def test_uncolored_mesh_rejected(setup):
    def mutation(response, request, job):
        Path(response["mesh"]).write_bytes(
            trimesh.Scene(trimesh.creation.box()).export(file_type="glb")
        )

    with pytest.raises(ContractError, match="vertex colors"):
        Open3DReconstruction(Path(sys.executable), worker=StubWorker(mutation)).reconstruct(*setup)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"voxel_size_ratio": 0},
        {"voxel_size_ratio": True},
        {"sdf_trunc_ratio": 0.001},
        {"depth_trunc_ratio": float("nan")},
        {"timeout_seconds": float("inf")},
        {"up_axis": "Y"},
    ],
)
def test_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        Open3DReconstruction(Path(sys.executable), **kwargs)


@pytest.mark.parametrize("field", ["world_frame", "unit"])
def test_response_spatial_metadata_must_match(setup, field):
    def mutation(response, request, job):
        response["backend_metadata"][field] = "wrong"

    with pytest.raises(ContractError, match="frame/unit differs"):
        Open3DReconstruction(Path(sys.executable), worker=StubWorker(mutation)).reconstruct(*setup)


def test_worker_failure_preserves_error_code(setup):
    failure = PipelineError(ErrorCode.BACKEND_TIMEOUT, "timed out", retryable=True)

    class FailingWorker:
        def run(self, job):
            raise failure

    with pytest.raises(PipelineError) as caught:
        Open3DReconstruction(Path(sys.executable), worker=FailingWorker()).reconstruct(*setup)
    assert caught.value is failure


def test_metadata_cannot_override_trusted_parameters(setup):
    def mutation(response, request, job):
        response["backend_metadata"].update(runner_digest="fake", up_axis="+X")

    result = Open3DReconstruction(Path(sys.executable), worker=StubWorker(mutation)).reconstruct(
        *setup
    )
    assert result.backend_metadata["parameters"]["runner_digest"].startswith("sha256:")
    assert result.backend_metadata["parameters"]["up_axis"] == "-Y"


def test_environment_change_during_reconstruction_rejected(setup, monkeypatch):
    identities = iter(
        [
            {"environment_digest": "sha256:" + "a" * 64},
            {"environment_digest": "sha256:" + "b" * 64},
        ]
    )
    monkeypatch.setattr(
        "assets_generator.backends.open3d_tsdf.open3d_environment_identity",
        lambda python: next(identities),
    )
    with pytest.raises(ContractError, match="environment changed"):
        Open3DReconstruction(Path(sys.executable), worker=StubWorker()).reconstruct(*setup)

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.backends.da3 import DA3GeometryFrontend
from assets_generator.contracts import ContractError
from assets_generator.errors import ErrorCode, PipelineError
from assets_generator.models import CameraRecord, ObservationView, SpatialTransform
from assets_generator.observations import make_observation_bundle, observation_bundle_value
from assets_generator.serialization import to_primitive
from assets_generator.worker import WorkerJob


class StubWorker:
    def __init__(self, mutation=None, failure=None):
        self.mutation = mutation
        self.failure = failure
        self.requests = []
        self.payloads = []

    def run(self, job):
        self.requests.append(job)
        if self.failure:
            raise self.failure
        request = json.loads(Path(job.command[-2]).read_text())
        self.payloads.append(request)
        output = Path(request["output_dir"])
        cameras, depths = [], []
        for index, view in enumerate(request["observations"]):
            with Image.open(view["image"]) as image:
                width, height = image.size
            frame = f"da3_camera_{index}"
            camera = CameraRecord(
                f"camera_{index}",
                view["view_id"],
                "pinhole",
                width,
                height,
                20.0,
                20.0,
                width / 2,
                height / 2,
                [],
                frame,
                SpatialTransform(frame, "da3_world", np.eye(4).tolist()),
                "estimated",
            )
            cameras.append(to_primitive(camera))
            path = output / f"depth_{index}.tiff"
            Image.fromarray(np.ones((height, width), dtype=np.float32)).save(path)
            depths.append({"view_id": view["view_id"], "path": str(path)})
        point_path = output / "points.ply"
        point_path.write_bytes(
            trimesh.points.PointCloud([[0, 0, 1], [1, 0, 2]]).export(file_type="ply")
        )
        response = {
            "cameras": cameras,
            "depths": depths,
            "points": str(point_path),
            "backend_metadata": {"diagnostic": "test", "model_digest": "untrusted"},
        }
        if self.mutation:
            self.mutation(response, request)
        Path(job.command[-1]).write_text(json.dumps(response))
        return WorkerJob("test_job", job.idempotency_key, "succeeded", "now")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = LocalArtifactStore(tmp_path / "store")
    views = []
    for view_id in ["left", "right"]:
        data = io.BytesIO()
        Image.new("RGB", (8, 6), "red").save(data, format="PNG")
        ref = store.persist_bytes(
            data.getvalue(), kind="rgb_image", schema_name="png", schema_version="1.0"
        )
        views.append(ObservationView(view_id, ref))
    observations = store.persist_structured(
        observation_bundle_value(make_observation_bundle(views, store))
    )
    model = tmp_path / "model"
    model.mkdir()
    (model / "weights").write_bytes(b"model")
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = tmp_path / "runner.py"
    runner.write_text("# test runner")
    monkeypatch.setattr(DA3GeometryFrontend, "_runner_path", lambda self: runner)
    monkeypatch.setattr(
        "assets_generator.backends.da3.backend_source_identity",
        lambda repo: {
            "revision": "test_revision",
            "source_digest": "sha256:" + "a" * 64,
            "dirty": False,
            "path": str(repo),
        },
    )
    return store, observations, repo, model


def test_da3_request_and_artifact_contract(setup, tmp_path):
    store, observations, repo, model = setup
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    worker = StubWorker()
    backend = DA3GeometryFrontend(python, repo, model, worker=worker)
    output = backend.estimate(store, observations)
    assert worker.requests[0].command[0] == str(python)
    assert worker.requests[0].cwd == repo
    assert worker.payloads[0]["repo"] == str(repo)
    assert worker.payloads[0]["model"] == str(model)
    assert worker.payloads[0]["process_res"] == 392
    assert [v["view_id"] for v in worker.payloads[0]["observations"]] == ["left", "right"]
    assert not Path(worker.requests[0].command[-1]).exists()
    assert len(output.cameras) == len(output.depths) == 2
    for index, depth in enumerate(output.depths):
        assert store.verify_digest(depth)
        identity = store.get_manifest(depth.artifact_id).identity
        assert (identity.kind, identity.schema_name) == ("depth_map", "tiff")
        assert identity.identity_metadata["frame_id"] == f"da3_camera_{index}"
        assert identity.identity_metadata["unit"] == "relative_unit"
        assert output.cameras[index].kind == "camera_record"
        assert output.cameras[index].value["source"] == "estimated"
    assert store.verify_digest(output.points)
    identity = store.get_manifest(output.points.artifact_id).identity
    assert identity.kind == "point_cloud"
    assert identity.identity_metadata["frame_id"] == "da3_world"
    assert identity.identity_metadata["unit"] == "relative_unit"
    assert output.backend_metadata["model_digest"].startswith("sha256:")
    assert output.backend_metadata["backend_source"]["revision"] == "test_revision"
    assert output.backend_metadata["parameters"]["process_res"] == 392
    assert not output.cache_hit


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda r, q: r["cameras"].pop(), "one camera and depth"),
        (lambda r, q: r["depths"][0].update(view_id="other"), "ordered input view_id"),
        (lambda r, q: r["cameras"][0].update(source="provided"), "estimated camera/world"),
        (
            lambda r, q: r["cameras"][0]["T_world_camera"].update(target_frame_id="wrong"),
            "camera/world",
        ),
        (lambda r, q: r["cameras"][0].update(width=100), "dimensions"),
        (lambda r, q: r.update(points=q["observations"][0]["image"]), "inside its work"),
        (lambda r, q: Path(r["points"]).write_bytes(b"bad ply"), "invalid DA3 response"),
        (lambda r, q: r.update(backend_metadata=[]), "backend_metadata"),
    ],
)
def test_da3_rejects_invalid_response(setup, mutation, match):
    store, observations, repo, model = setup
    backend = DA3GeometryFrontend(Path(sys.executable), repo, model, worker=StubWorker(mutation))
    with pytest.raises(ContractError, match=match):
        backend.estimate(store, observations)


@pytest.mark.parametrize(
    "values", [np.zeros((6, 8), np.float32), np.full((6, 8), np.nan, np.float32)]
)
def test_da3_rejects_invalid_depth(setup, values):
    store, observations, repo, model = setup

    def mutate(response, request):
        Image.fromarray(values).save(response["depths"][0]["path"])

    backend = DA3GeometryFrontend(Path(sys.executable), repo, model, worker=StubWorker(mutate))
    with pytest.raises(ContractError, match="valid depth"):
        backend.estimate(store, observations)


def test_da3_worker_failure_preserves_error_code(setup):
    store, observations, repo, model = setup
    failure = PipelineError(ErrorCode.BACKEND_TIMEOUT, "timed out", retryable=True)
    backend = DA3GeometryFrontend(
        Path(sys.executable), repo, model, worker=StubWorker(failure=failure)
    )
    with pytest.raises(PipelineError) as caught:
        backend.estimate(store, observations)
    assert caught.value is failure


def test_da3_rejects_model_mutation(setup):
    store, observations, repo, model = setup

    def mutate(response, request):
        (model / "weights").write_bytes(b"changed model")

    backend = DA3GeometryFrontend(Path(sys.executable), repo, model, worker=StubWorker(mutate))
    with pytest.raises(ContractError, match="changed during inference"):
        backend.estimate(store, observations)


def test_da3_checks_input_digest_before_worker(setup):
    store, observations, repo, model = setup
    store.blob_path(observations).write_bytes(b"corrupt")
    worker = StubWorker()
    backend = DA3GeometryFrontend(Path(sys.executable), repo, model, worker=worker)
    with pytest.raises(ContractError, match="invalid artifact digest"):
        backend.estimate(store, observations)
    assert worker.requests == []


@pytest.mark.parametrize("process_res", [True, 0, 13, 392.5])
def test_da3_invalid_process_resolution(setup, process_res):
    _, _, repo, model = setup
    with pytest.raises(ValueError, match="process_res"):
        DA3GeometryFrontend(Path(sys.executable), repo, model, process_res=process_res)


def test_da3_invalid_json_response(setup):
    store, observations, repo, model = setup

    class BadJSONWorker(StubWorker):
        def run(self, request):
            result = super().run(request)
            Path(request.command[-1]).write_text("{")
            return result

    backend = DA3GeometryFrontend(Path(sys.executable), repo, model, worker=BadJSONWorker())
    with pytest.raises(ContractError, match="invalid DA3 response"):
        backend.estimate(store, observations)


def test_da3_processing_and_environment_survive_provenance_projection(setup):
    from assets_generator.multi_view_workflow import _provenance_parameters

    store, observations, repo, model = setup
    processing = {
        "process_res_method": "upper_bound_resize",
        "preprocessing": [{"original_size": [8, 6], "resized_size": [8, 6]}],
        "depth_resampling": "nearest-original-pixel-coordinates",
        "invalid_value": 0,
        "confidence_percentile": 40,
        "confidence_threshold": 0.3,
        "point_sampling_stride": 1,
        "unit": "relative_unit",
        "world_frame": "da3_world",
        "software_versions": {"torch": "test-torch", "depth-anything-3": "test-da3"},
        "python_version": "test-python",
        "cuda_version": "test-cuda",
    }

    def mutate(response, request):
        response["backend_metadata"].update(processing)
        response["backend_metadata"].update(
            process_res=999, inference_and_load_seconds=100, peak_cuda_memory_mb=200
        )

    backend = DA3GeometryFrontend(Path(sys.executable), repo, model, worker=StubWorker(mutate))
    output = backend.estimate(store, observations)
    inference = _provenance_parameters(output.backend_metadata)["inference"]
    for key, value in processing.items():
        assert inference[key] == value
    assert inference["process_res"] == 392
    assert inference["input_mode"] == "rgb_only"
    assert inference["runner_digest"].startswith("sha256:")
    assert "inference_and_load_seconds" not in inference
    assert "peak_cuda_memory_mb" not in inference

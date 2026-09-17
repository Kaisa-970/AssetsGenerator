import io
import json
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.backends.sam_instances import SAMInstanceProposer
from assets_generator.contracts import ContractError


class Worker:
    def __init__(self, mutate=None):
        self.mutate = mutate

    def run(self, job):
        request = json.loads((job.cwd / "request.json").read_text())
        assert request["checkpoint"].endswith("checkpoint.pth")
        values = np.zeros((6, 8), dtype=np.uint8)
        values[1:5, 2:7] = 255
        path = job.cwd / "mask.png"
        Image.fromarray(values).save(path)
        response = {
            "proposals": [
                {
                    "mask": str(path),
                    "area": 20,
                    "bbox": [2, 1, 5, 4],
                    "predicted_iou": 0.99,
                    "stability_score": 0.99,
                }
            ],
            "backend_metadata": {
                "software_versions": {"sam": "test"},
                "backend_environment": {"environment_digest": "sha256:test"},
                "parameters": {
                    "points_per_side": 16,
                    "max_instances": 20,
                    "min_area_pixels": 1,
                    "pred_iou_thresh": 0.88,
                    "stability_score_thresh": 0.95,
                    "points_per_batch": 64,
                    "crop_n_layers": 0,
                    "output_mode": "binary_mask",
                },
            },
        }
        if self.mutate:
            self.mutate(response, path)
        (job.cwd / "response.json").write_text(json.dumps(response))
        return SimpleNamespace(job_id="test-job")


def setup(tmp_path, mutate=None):
    store = LocalArtifactStore(tmp_path / "store")
    data = io.BytesIO()
    Image.new("RGB", (8, 6)).save(data, format="PNG")
    image = store.persist_bytes(
        data.getvalue(), kind="rgb_image", schema_name="png", schema_version="1.0"
    )
    checkpoint = tmp_path / "checkpoint.pth"
    checkpoint.write_bytes(b"fake model")
    backend = SAMInstanceProposer(
        tmp_path / "venv/bin/python", checkpoint, min_area_pixels=1, worker=Worker(mutate)
    )
    return store, image, backend


def test_sam_persisted_masks_and_stable_identity(tmp_path):
    store, image, backend = setup(tmp_path)
    first = backend.propose(store, image)
    second = backend.propose(store, image)
    assert first.kind == "instance_proposals"
    assert first.value["proposals"] == second.value["proposals"]
    row = first.value["proposals"][0]
    assert row["label"] == "unknown" and row["source"] == "estimated"
    assert row["bbox"] == [2, 1, 5, 4]
    from assets_generator.models import ArtifactRef

    assert store.verify_digest(ArtifactRef(**row["mask"]))
    from assets_generator.workflow import _import_image

    mask = ArtifactRef(**row["mask"])
    assert _import_image(store, store.blob_path(mask), "binary_mask") == mask
    metadata = first.value["backend_metadata"]
    assert metadata["model_digest"].startswith("sha256:")
    assert metadata["checkpoint_digest"] == metadata["model_digest"]
    assert metadata["runner_digest"].startswith("sha256:")
    assert metadata["configured_python"].endswith("venv/bin/python")
    assert metadata["parameters"]["points_per_batch"] == 64
    assert metadata["configured_parameters"]["model_type"] == "vit_h"
    assert metadata["worker_job_id"] == "test-job"


@pytest.mark.parametrize(
    "problem",
    [
        "escape",
        "symlink",
        "empty",
        "nonbinary",
        "size",
        "area",
        "bbox",
        "score",
        "nan",
        "duplicates",
        "environment",
        "parameters",
    ],
)
def test_sam_rejects_invalid_worker_outputs(tmp_path, problem):
    def mutate(response, path):
        row = response["proposals"][0]
        if problem in {"escape", "symlink"}:
            outside = tmp_path / "outside.png"
            outside.write_bytes(path.read_bytes())
            if problem == "escape":
                row["mask"] = str(outside)
            else:
                path.unlink()
                path.symlink_to(outside)
        elif problem in {"empty", "nonbinary", "size"}:
            Image.new(
                "L", (9, 6) if problem == "size" else (8, 6), 128 if problem == "nonbinary" else 0
            ).save(path)
        elif problem == "area":
            row["area"] = 19
        elif problem == "bbox":
            row["bbox"] = [0, 0, 8, 6]
        elif problem == "score":
            row["predicted_iou"] = True
        elif problem == "nan":
            row["stability_score"] = float("nan")
        elif problem == "duplicates":
            response["proposals"].append(dict(row))
        elif problem == "environment":
            del response["backend_metadata"]["backend_environment"]
        elif problem == "parameters":
            response["backend_metadata"]["parameters"]["points_per_batch"] = 1

    store, image, backend = setup(tmp_path, mutate)
    with pytest.raises(ContractError):
        backend.propose(store, image)


def test_sam_rejects_checkpoint_changed_by_worker(tmp_path):
    def mutate(response, path):
        (tmp_path / "checkpoint.pth").write_bytes(b"changed")

    store, image, backend = setup(tmp_path, mutate)
    with pytest.raises(ContractError, match="changed"):
        backend.propose(store, image)


def test_sam_empty_proposal_list_is_valid(tmp_path):
    store, image, backend = setup(tmp_path, lambda response, path: response.update(proposals=[]))
    assert backend.propose(store, image).value["proposals"] == []

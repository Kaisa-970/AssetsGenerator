"""Isolated SAM automatic mask proposals; no semantic detection or inferred poses."""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..artifact_store import LocalArtifactStore
from ..contracts import ContractError
from ..models import ArtifactRef, StructuredValue
from ..serialization import canonical_json_bytes, sha256_bytes, to_primitive
from ..worker import LocalProcessWorker, ProcessJobRequest
from ..workflow import _import_image


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return "sha256:" + value.hexdigest()


class SAMInstanceProposer:
    def __init__(
        self,
        python: Path,
        checkpoint: Path,
        *,
        model_type: str = "vit_h",
        device: str = "cuda",
        points_per_side: int = 16,
        max_instances: int = 20,
        min_area_pixels: int = 64,
        pred_iou_thresh: float = 0.88,
        stability_score_thresh: float = 0.95,
        timeout_seconds: float = 1800,
        worker: LocalProcessWorker | None = None,
    ) -> None:
        if model_type not in {"vit_h", "vit_l", "vit_b"} or device not in {"cuda", "cpu"}:
            raise ValueError("invalid SAM model_type/device")
        for name, value in [
            ("points_per_side", points_per_side),
            ("max_instances", max_instances),
            ("min_area_pixels", min_area_pixels),
        ]:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name, threshold in [
            ("pred_iou_thresh", pred_iou_thresh),
            ("stability_score_thresh", stability_score_thresh),
        ]:
            if (
                isinstance(threshold, bool)
                or not math.isfinite(threshold)
                or not 0 <= threshold <= 1
            ):
                raise ValueError(f"{name} must be between zero and one")
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        self.python = python.expanduser().absolute()
        self.checkpoint = checkpoint.expanduser().absolute()
        self.parameters: dict[str, Any] = dict(
            model_type=model_type,
            device=device,
            points_per_side=points_per_side,
            max_instances=max_instances,
            min_area_pixels=min_area_pixels,
            pred_iou_thresh=pred_iou_thresh,
            stability_score_thresh=stability_score_thresh,
        )
        self.timeout_seconds = timeout_seconds
        self.worker = worker or LocalProcessWorker()

    def propose(self, store: LocalArtifactStore, image: ArtifactRef) -> StructuredValue:
        if not store.verify_digest(image) or store.get_manifest(
            image.artifact_id
        ).identity.kind not in {"rgb_image", "rgba_image"}:
            raise ContractError("SAM requires a valid image artifact")
        with Image.open(store.blob_path(image)) as source:
            source.load()
            size = source.size
        runner = Path(__file__).with_name("sam_instances_runner.py")
        model_digest, runner_digest = _digest(self.checkpoint), _digest(runner)
        with tempfile.TemporaryDirectory(prefix="sam-instances-", dir=store.root) as temporary:
            work = Path(temporary).resolve()
            request_path, response_path = work / "request.json", work / "response.json"
            request_path.write_text(
                json.dumps(
                    {
                        **self.parameters,
                        "image": str(store.blob_path(image).resolve()),
                        "checkpoint": str(self.checkpoint),
                        "output_dir": str(work),
                    }
                )
            )
            job = self.worker.run(
                ProcessJobRequest(
                    [str(self.python), str(runner), str(request_path), str(response_path)],
                    work,
                    self.timeout_seconds,
                    request_path.as_uri(),
                )
            )
            if model_digest != _digest(self.checkpoint) or runner_digest != _digest(runner):
                raise ContractError("SAM model or runner changed during execution")
            try:
                response = json.loads(response_path.read_text())
                proposals, metadata = self._response(store, image, response, work, size)
            except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
                raise ContractError(f"invalid SAM response: {error}") from error
        metadata.update(
            model_digest=model_digest,
            checkpoint_digest=model_digest,
            runner_digest=runner_digest,
            configured_python=str(self.python),
            configured_parameters=self.parameters,
            worker_job_id=job.job_id,
            backend_version="sam-automatic-mask-v1",
        )
        return StructuredValue(
            "instance_proposals",
            "InstanceProposals",
            "1.0",
            {
                "schema_version": "1.0",
                "image": to_primitive(image),
                "proposals": proposals,
                "status": "unreviewed_proposals",
                "backend_metadata": metadata,
            },
        )

    def _response(
        self,
        store: LocalArtifactStore,
        image: ArtifactRef,
        response: Any,
        work: Path,
        size: tuple[int, int],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows = response["proposals"]
        metadata = response["backend_metadata"]
        if not isinstance(rows, list) or len(rows) > self.parameters["max_instances"]:
            raise ContractError("SAM proposals must be a bounded list")
        if (
            not isinstance(metadata, dict)
            or not isinstance(metadata.get("software_versions"), dict)
            or not metadata["software_versions"]
        ):
            raise ContractError("SAM requires software version metadata")
        environment = metadata.get("backend_environment")
        actual_parameters = metadata.get("parameters")
        expected_parameters = {
            key: value
            for key, value in self.parameters.items()
            if key not in {"model_type", "device"}
        } | {
            "points_per_batch": min(64, self.parameters["points_per_side"] ** 2),
            "crop_n_layers": 0,
            "output_mode": "binary_mask",
        }
        if (
            not isinstance(environment, dict)
            or not isinstance(environment.get("environment_digest"), str)
            or not environment["environment_digest"].startswith("sha256:")
            or actual_parameters != expected_parameters
        ):
            raise ContractError("SAM requires environment identity and exact execution parameters")
        proposals = []
        ids = set()
        for row in rows:
            path = (work / row["mask"]).resolve()
            if not path.is_relative_to(work) or not path.is_file():
                raise ContractError("SAM mask must remain inside work directory")
            with Image.open(path) as mask:
                values = np.asarray(mask)
                if (
                    mask.format != "PNG"
                    or mask.mode != "L"
                    or mask.size != size
                    or not np.isin(values, [0, 255]).all()
                    or not np.any(values)
                ):
                    raise ContractError("SAM mask must be nonempty binary PNG matching image size")
            y, x = np.nonzero(values)
            area = int(len(x))
            bbox = [
                int(x.min()),
                int(y.min()),
                int(x.max() - x.min() + 1),
                int(y.max() - y.min() + 1),
            ]
            if (
                isinstance(row["area"], bool)
                or row["area"] != area
                or row["bbox"] != bbox
                or area < self.parameters["min_area_pixels"]
            ):
                raise ContractError("SAM area/bbox must match mask pixels")
            for score in ["predicted_iou", "stability_score"]:
                value = row[score]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    raise ContractError("SAM scores must be finite numbers")
            if (
                row["predicted_iou"] < self.parameters["pred_iou_thresh"]
                or row["stability_score"] < self.parameters["stability_score_thresh"]
            ):
                raise ContractError("SAM proposal score is below configured threshold")
            ref = _import_image(store, path, "binary_mask")
            proposal_id = sha256_bytes(
                canonical_json_bytes(["sam-proposal-v1", image.artifact_id, ref.artifact_id])
            )
            if proposal_id in ids:
                raise ContractError("SAM returned duplicate masks")
            ids.add(proposal_id)
            proposals.append(
                {
                    "proposal_id": proposal_id,
                    "mask": to_primitive(ref),
                    "area": area,
                    "bbox": bbox,
                    "bbox_convention": "xywh_pixel_extent",
                    "predicted_iou": row["predicted_iou"],
                    "stability_score": row["stability_score"],
                    "label": "unknown",
                    "source": "estimated",
                }
            )
        return proposals, dict(metadata)

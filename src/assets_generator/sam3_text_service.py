"""SAM3 handler for the existing unified remote protocol, using an isolated runner."""

import json
import math
import tempfile
from pathlib import Path

from .remote_protocol import RemoteIdentity, RemoteRequest
from .remote_service_process import ServiceProcessWorker
from .remote_service_store import RemoteServiceStore
from .remote_service_worker import ServiceOutput
from .sam3_text_contract import image_size, parameters, validate_mask
from .serialization import canonical_json_bytes, sha256_bytes
from .worker import ProcessJobRequest


class Sam3TextHandler:
    def __init__(
        self,
        identity: RemoteIdentity,
        python: Path,
        repo: Path,
        checkpoint: Path,
        runner: Path,
        workspace: Path,
    ):
        self.identity, self.python, self.repo = identity, python, repo
        self.checkpoint, self.runner, self.workspace = checkpoint, runner, workspace

    def __call__(
        self, request: RemoteRequest, service: RemoteServiceStore
    ) -> dict[str, ServiceOutput]:
        if request.identity != self.identity or service.identity != self.identity:
            raise ValueError("SAM3 service identity mismatch")
        payload = json.loads(request.payload_json)
        if (
            set(payload)
            != {"operation", "parameters", "input_blobs", "input_digest", "binding_digest"}
            or payload["operation"] != "text_segmentation@1"
        ):
            raise ValueError("invalid text segmentation payload")
        options = parameters(payload["parameters"])
        uploads = payload["input_blobs"]
        if not isinstance(uploads, dict) or set(uploads) != {"image"}:
            raise ValueError("text segmentation requires image upload")
        descriptor = uploads["image"]
        if not isinstance(descriptor, dict) or set(descriptor) != {"artifact_id", "identity"}:
            raise ValueError("invalid image descriptor")
        identity = descriptor["identity"]
        if not isinstance(identity, dict) or set(identity) != {
            "kind",
            "schema_name",
            "schema_version",
            "blob_digest",
            "identity_metadata",
        }:
            raise ValueError("invalid image identity")
        if (
            identity["kind"] != "rgb_image"
            or identity["schema_version"] != "1.0"
            or sha256_bytes(canonical_json_bytes(identity)) != descriptor["artifact_id"]
        ):
            raise ValueError("image identity mismatch")
        data = service.get_blob(identity["blob_digest"])
        size = image_size(data)
        self.workspace.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.workspace) as temporary:
            root = Path(temporary)
            (root / "image").write_bytes(data)
            (root / "parameters.json").write_bytes(canonical_json_bytes(options))
            ServiceProcessWorker(service, request).run(
                ProcessJobRequest(
                    [
                        str(self.python),
                        str(self.runner),
                        "--checkpoint",
                        str(self.checkpoint),
                        "--image",
                        str(root / "image"),
                        "--parameters",
                        str(root / "parameters.json"),
                        "--output",
                        str(root / "result"),
                    ],
                    self.repo,
                    600,
                    request.submission_key,
                )
            )
            items = json.loads((root / "result/candidates.json").read_bytes())
            if not isinstance(items, list) or len(items) > 64:
                raise ValueError("invalid candidates")
            outputs = {}
            candidates = []
            for index, item in enumerate(items):
                name = f"mask_{index}"
                if item["output_id"] != name:
                    raise ValueError("candidate index mismatch")
                score = item["score"]
                box = item["box"]
                if (
                    type(score) not in (int, float)
                    or not math.isfinite(score)
                    or not 0 <= score <= 1
                    or not isinstance(box, list)
                    or len(box) != 4
                    or not all(type(x) in (int, float) and math.isfinite(x) for x in box)
                ):
                    raise ValueError("invalid candidate score/box")
                mask = (root / "result" / (name + ".png")).read_bytes()
                validate_mask(mask, size)
                outputs[name] = ServiceOutput(mask, "image/png")
                candidates.append({**item, "blob_digest": sha256_bytes(mask)})
            evidence = {
                "schema": "Sam3TextCandidates@1",
                "image_artifact_id": descriptor["artifact_id"],
                "image_digest": identity["blob_digest"],
                "parameters": options,
                "backend_digest": self.identity.backend_digest,
                "request_digest": request.request_digest,
                "candidates": candidates,
            }
            outputs["evidence"] = ServiceOutput(canonical_json_bytes(evidence), "application/json")
            return outputs

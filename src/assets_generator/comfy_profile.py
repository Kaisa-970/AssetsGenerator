"""Trusted local configuration for an opaque ComfyUI image workflow.

Loading validates declarations only; it neither contacts the service nor attests
installed nodes/models. This is not yet a registered Core Operator or DAG adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .artifact_store import LocalArtifactStore
    from .models import ArtifactRef
    from .remote_protocol import RemoteJob, RemoteRequest
    from .remote_service_store import RemoteServiceStore

from .comfy_http import ComfyClient
from .comfy_workflow import ComfyWorkflow
from .dag_adapters import AdapterSpec
from .remote_protocol import RemoteIdentity, decode_remote_json
from .serialization import canonical_json_bytes, sha256_bytes


@dataclass(frozen=True)
class ComfyImageProfile:
    body: bytes

    def __post_init__(self) -> None:
        raw = decode_remote_json(self.body)
        if not isinstance(raw, dict) or set(raw) != {
            "schema",
            "service_id",
            "endpoint",
            "prompt",
            "parameter_schema",
            "defaults",
            "parameter_targets",
            "image_targets",
            "output",
            "deployment_claims",
        }:
            raise ValueError("invalid ComfyUI image profile fields")
        if raw["schema"] != "comfy-image-profile@1":
            raise ValueError("unsupported ComfyUI image profile schema")
        endpoint = ComfyClient(raw["endpoint"]).endpoint
        if endpoint != raw["endpoint"]:
            raise ValueError("ComfyUI profile endpoint must be canonical")
        RemoteIdentity(raw["service_id"], sha256_bytes(self.body))
        if not isinstance(raw["deployment_claims"], dict):
            raise ValueError("ComfyUI deployment claims must be an object")
        workflow = self.workflow()
        output = raw["output"]
        if (
            not isinstance(output, dict)
            or set(output) != {"node", "index", "mode"}
            or not isinstance(output["node"], str)
            or output["node"] not in workflow.prompt
            or type(output["index"]) is not int
            or output["index"] < 0
            or output["mode"] not in {"RGB", "RGBA"}
        ):
            raise ValueError("invalid ComfyUI image output mapping")
        object.__setattr__(self, "body", canonical_json_bytes(raw))

    @classmethod
    def load(cls, path: Path) -> ComfyImageProfile:
        with path.open("rb") as stream:
            body = stream.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise ValueError("ComfyUI image profile exceeds size limit")
        return cls(body)

    @property
    def identity(self) -> RemoteIdentity:
        return RemoteIdentity(self.to_dict()["service_id"], sha256_bytes(self.body))

    def to_dict(self) -> dict[str, Any]:
        value = decode_remote_json(self.body)
        if not isinstance(value, dict):
            raise ValueError("ComfyUI profile must be an object")
        return value  # Detached editable copy.

    def workflow(self) -> ComfyWorkflow:
        raw = self.to_dict()

        def targets(key: str) -> dict[str, tuple[str, str]]:
            mapping = raw[key]
            if not isinstance(mapping, dict):
                raise ValueError("ComfyUI target mapping must be an object")
            result = {}
            for name, target in mapping.items():
                if (
                    not isinstance(target, list)
                    or len(target) != 2
                    or any(not isinstance(v, str) for v in target)
                ):
                    raise ValueError("ComfyUI target requires [node, input]")
                result[name] = (target[0], target[1])
            return result

        return ComfyWorkflow(
            raw["prompt"],
            AdapterSpec(
                "comfy_image_profile",
                "1",
                ("comfy_image_boundary@1",),
                raw["parameter_schema"],
                raw["defaults"],
                execution_kind="remote",
            ),
            targets("parameter_targets"),
            image_targets=targets("image_targets"),
        )

    def request(
        self, key: str, *, images: dict[str, ArtifactRef], parameters: dict[str, Any]
    ) -> RemoteRequest:
        from .compiled_plan import thaw
        from .remote_protocol import RemoteRequest

        workflow = self.workflow()
        if set(images) != set(workflow.image_targets):
            raise ValueError("ComfyUI request image ports differ from profile")
        return RemoteRequest.create(
            self.identity,
            key,
            {
                "images": {name: {"artifact_id": ref.artifact_id} for name, ref in images.items()},
                "parameters": thaw(workflow.spec.normalize_parameters(parameters)),
            },
        )

    def start(
        self,
        owner: RemoteServiceStore,
        request: RemoteRequest,
        journal_path: Path,
        store: LocalArtifactStore,
    ) -> dict[str, Any]:
        from .comfy_service import start_owned_image
        from .models import ArtifactRef

        if request.identity != self.identity:
            raise ValueError("ComfyUI request targets another profile")
        payload = decode_remote_json(request.payload_json)
        if set(payload) != {"images", "parameters"} or not isinstance(payload["images"], dict):
            raise ValueError("invalid ComfyUI request payload")
        images = {}
        for name, value in payload["images"].items():
            if not isinstance(value, dict) or set(value) != {"artifact_id"}:
                raise ValueError("invalid ComfyUI input ArtifactRef")
            images[name] = ArtifactRef(value["artifact_id"])
        if (
            self.request(request.submission_key, images=images, parameters=payload["parameters"])
            != request
        ):
            raise ValueError("ComfyUI request parameters must be normalized")
        raw = self.to_dict()
        return start_owned_image(
            owner,
            request,
            journal_path,
            ComfyClient(raw["endpoint"]),
            store,
            self.workflow(),
            images=images,
            parameters=payload["parameters"],
            deployment_claims={
                "profile_digest": self.identity.backend_digest,
                "declared": raw["deployment_claims"],
            },
        )

    def finish(
        self,
        owner: RemoteServiceStore,
        request: RemoteRequest,
        journal_path: Path,
        store: LocalArtifactStore,
    ) -> RemoteJob:
        from .comfy_result import publish_image_result
        from .comfy_service import finish_owned_image

        if request.identity != self.identity:
            raise ValueError("ComfyUI request targets another profile")
        raw = self.to_dict()
        finish_owned_image(
            owner, request, journal_path, ComfyClient(raw["endpoint"]), store, **raw["output"]
        )
        return publish_image_result(owner, request, store)

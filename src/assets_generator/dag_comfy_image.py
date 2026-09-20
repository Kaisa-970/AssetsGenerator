"""Remote RGB transform adapter; verify the opaque workflow boundary on import."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .comfy_output import validate_png
from .comfy_profile import ComfyImageProfile
from .compiled_plan import thaw
from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .dag_remote_adapter import RemoteNodeAdapter
from .models import ArtifactRef
from .remote_http import RemoteJobClient
from .remote_protocol import RemoteJob, RemoteOutput, decode_remote_json
from .serialization import canonical_json_bytes, sha256_bytes
from .workbench_persistence import _references


class ComfyImageAdapter(RemoteNodeAdapter):
    def __init__(self, endpoint: str, profile: ComfyImageProfile):
        workflow = profile.workflow()
        if set(workflow.image_targets) != {"image"} or profile.to_dict()["output"]["mode"] != "RGB":
            raise ValueError("image_transform requires one RGB image boundary")
        fixed = {
            "remote_endpoint": RemoteJobClient(endpoint).endpoint,
            "service_id": profile.identity.service_id,
            "backend_digest": profile.identity.backend_digest,
        }
        schema = thaw(workflow.spec.parameter_schema)
        if set(schema.get("properties", {})) & set(fixed):
            raise ValueError("ComfyUI parameter conflicts with remote binding")
        schema["properties"] = {
            **schema.get("properties", {}),
            **{k: {"type": "string", "enum": [v]} for k, v in fixed.items()},
        }
        self.profile = profile
        self._spec = AdapterSpec(
            "comfy_image",
            "1",
            ("image_transform@1",),
            schema,
            {**thaw(workflow.spec.defaults), **fixed},
            execution_kind="remote",
        )

    @property
    def spec(self) -> AdapterSpec:
        return self._spec

    def input_blobs(self, context: NodeExecutionContext) -> Mapping[str, ArtifactRef]:
        image = context.inputs.get("image")
        if not isinstance(image, ArtifactRef):
            raise ContractError("ComfyUI requires image ArtifactRef")
        return {"image": image}

    def prepare_payload(self, context: NodeExecutionContext) -> dict[str, Any]:
        actual = {
            k: v
            for k, v in context.parameters.items()
            if k not in {"remote_endpoint", "service_id", "backend_digest"}
        }
        return {
            "operation": "image_transform@1",
            "parameters": thaw(self.profile.workflow().spec.normalize_parameters(actual)),
        }

    def import_result(
        self, context: NodeExecutionContext, job: RemoteJob, blobs: Mapping[str, bytes]
    ) -> NodeExecutionResult:
        if set(blobs) != {"image", "evidence"}:
            raise ContractError("ComfyUI requires image and evidence outputs")
        for name, media in (("image", "image/png"), ("evidence", "application/json")):
            descriptor = RemoteOutput.from_job(job, name)
            if (
                descriptor.media_type != media
                or descriptor.byte_length != len(blobs[name])
                or descriptor.blob_digest != sha256_bytes(blobs[name])
            ):
                raise ContractError("ComfyUI output transport mismatch")
        evidence = decode_remote_json(blobs["evidence"])
        source = self.input_blobs(context)["image"]
        raw = self.profile.to_dict()
        parameters = self.prepare_payload(context)["parameters"]
        if (
            evidence["provenance_scope"] != "composite_boundary_only"
            or evidence["inputs"] != {"image": {"artifact_id": source.artifact_id}}
            or evidence["submission"]["submission_key"] != job.job_id
            or evidence["output_mapping"] != raw["output"]
            or evidence["deployment_claims"]["endpoint"] != raw["endpoint"]
            or evidence["deployment_claims"]["claims"]["profile_digest"]
            != self.profile.identity.backend_digest
            or evidence["deployment_claims"]["claims"]["dag_input_digest"] != context.input_digest
            or evidence["internal_verification"]
            != {
                "comfy_revision": "unverified",
                "custom_nodes": "unverified",
                "models": "unverified",
            }
        ):
            raise ContractError("ComfyUI boundary differs from DAG request")
        bound = self.profile.workflow().bind(
            parameters, images=evidence["workflow"]["images"], endpoint=raw["endpoint"]
        )
        if bound != evidence["workflow"] or bound != evidence["deployment_claims"]["workflow"]:
            raise ContractError("ComfyUI executed workflow differs from profile")
        upload = bound["images"]["image"]
        if (
            upload["artifact_id"] != source.artifact_id
            or upload["blob_digest"]
            != context.store.get_manifest(source.artifact_id).identity.blob_digest
        ):
            raise ContractError("ComfyUI uploaded image differs from input")
        validate_png(blobs["image"], mode="RGB")
        identity: dict[str, Any] = {
            "kind": "rgb_image",
            "schema_name": "png",
            "schema_version": "1.0",
            "blob_digest": sha256_bytes(blobs["image"]),
            "identity_metadata": {"media_type": "image/png", "channel_layout": "RGB"},
        }
        output = ArtifactRef(sha256_bytes(canonical_json_bytes(identity)))
        if evidence["outputs"] != {"image": {"artifact_id": output.artifact_id}}:
            raise ContractError("ComfyUI image differs from boundary evidence")
        if set(_references(evidence)) - {source, output}:
            raise ContractError("ComfyUI evidence contains unavailable external references")
        if not context.store.verify_digest(source):
            raise ContractError("ComfyUI source evidence missing")
        image = context.store.persist_bytes(
            blobs["image"],
            kind="rgb_image",
            schema_name="png",
            schema_version="1.0",
            identity_metadata=identity["identity_metadata"],
        )
        proof = context.store.persist_bytes(
            blobs["evidence"],
            kind="remote_job_result",
            schema_name="ComfyImageBoundary",
            schema_version="1.0",
            identity_metadata={"media_type": "application/json"},
        )
        return NodeExecutionResult({"image": image, "evidence": proof})

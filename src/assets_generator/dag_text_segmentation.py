"""Text segmentation and explicit candidate selection, independent of DAG scheduling."""

import io
import math
from collections.abc import Mapping
from typing import Any

from PIL import Image, ImageChops

from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .dag_remote_adapter import RemoteNodeAdapter
from .models import ArtifactRef, StructuredValue
from .remote_http import RemoteJobClient
from .remote_protocol import (
    RemoteIdentity,
    RemoteJob,
    RemoteOutput,
    RemoteRequest,
    decode_remote_json,
)
from .sam3_text_contract import image_size, parameters, validate_mask
from .serialization import sha256_bytes


class RemoteTextSegmentationAdapter(RemoteNodeAdapter):
    def __init__(self, endpoint: str, identity: RemoteIdentity):
        fixed = {
            "remote_endpoint": RemoteJobClient(endpoint).endpoint,
            "service_id": identity.service_id,
            "backend_digest": identity.backend_digest,
        }
        self._spec = AdapterSpec(
            "remote_text_segmentation",
            "1",
            ("text_segmentation@1",),
            execution_kind="remote",
            parameter_schema={
                "type": "object",
                "properties": {
                    **{k: {"type": "string", "enum": [v]} for k, v in fixed.items()},
                    "prompt": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0.01, "maximum": 0.99},
                },
            },
            defaults={**fixed, "prompt": "object", "confidence": 0.5},
        )

    @property
    def spec(self) -> AdapterSpec:
        return self._spec

    def input_blobs(self, context: NodeExecutionContext) -> Mapping[str, ArtifactRef]:
        image = context.inputs.get("image")
        if not isinstance(image, ArtifactRef) or not context.store.verify_digest(image):
            raise ContractError("text segmentation requires intact image")
        image_size(context.store.blob_path(image).read_bytes())
        return {"image": image}

    def prepare_payload(self, context: NodeExecutionContext) -> dict[str, Any]:
        return {
            "operation": "text_segmentation@1",
            "parameters": parameters(
                {
                    "prompt": context.parameters["prompt"],
                    "confidence": context.parameters["confidence"],
                }
            ),
        }

    def import_result(
        self, context: NodeExecutionContext, job: RemoteJob, blobs: Mapping[str, bytes]
    ) -> NodeExecutionResult:
        image = self.input_blobs(context)["image"]
        payload = self.prepare_payload(context)
        from .serialization import to_primitive

        payload.update(
            input_digest=context.input_digest,
            binding_digest=context.binding_digest,
            input_blobs={
                "image": {
                    "artifact_id": image.artifact_id,
                    "identity": to_primitive(
                        context.store.get_manifest(image.artifact_id).identity
                    ),
                }
            },
        )
        request = RemoteRequest.create(
            RemoteIdentity(
                str(context.parameters["service_id"]), str(context.parameters["backend_digest"])
            ),
            job.job_id,
            payload,
        )
        raw = decode_remote_json(blobs["evidence"])
        if (
            not isinstance(raw, dict)
            or raw.get("schema") != "Sam3TextCandidates@1"
            or raw.get("image_artifact_id") != image.artifact_id
            or raw.get("image_digest")
            != context.store.get_manifest(image.artifact_id).identity.blob_digest
            or raw.get("parameters") != self.prepare_payload(context)["parameters"]
            or raw.get("backend_digest") != context.parameters["backend_digest"]
            or raw.get("request_digest") != request.request_digest
        ):
            raise ContractError("text segmentation evidence mismatch")
        items = raw.get("candidates")
        if (
            not isinstance(items, list)
            or len(items) > 64
            or set(blobs) != {"evidence", *(f"mask_{i}" for i in range(len(items)))}
        ):
            raise ContractError("invalid candidate output set")
        if RemoteOutput.from_job(job, "evidence").media_type != "application/json":
            raise ContractError("invalid candidate evidence media")
        size = image_size(context.store.blob_path(image).read_bytes())
        refs = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise ContractError("invalid candidate entry")
            score, box = item.get("score"), item.get("box")
            if (
                not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not math.isfinite(score)
                or not 0 <= score <= 1
                or not isinstance(box, list)
                or len(box) != 4
                or not all(type(v) in (int, float) and math.isfinite(v) for v in box)
            ):
                raise ContractError("invalid candidate score/box")
            name = f"mask_{i}"
            if (
                item["output_id"] != name
                or sha256_bytes(blobs[name]) != item["blob_digest"]
                or RemoteOutput.from_job(job, name).media_type != "image/png"
            ):
                raise ContractError("candidate mask identity mismatch")
            validate_mask(blobs[name], size)
            ref = context.store.persist_bytes(
                blobs[name],
                kind="binary_mask",
                schema_name="png",
                schema_version="1.0",
                identity_metadata={"media_type": "image/png"},
            )
            refs.append({**item, "mask": {"artifact_id": ref.artifact_id}})
        bundle = context.store.persist_structured(
            StructuredValue(
                "remote_job_result",
                "TextMaskCandidates",
                "1.0",
                {"image": {"artifact_id": image.artifact_id}, "evidence": raw, "candidates": refs},
            )
        )
        if not refs:
            raise ContractError("没有找到符合提示词和阈值的对象，请修改提示词或阈值")
        union = Image.new("L", size, 0)
        for entry in refs:
            with Image.open(context.store.blob_path(ArtifactRef(**entry["mask"]))) as candidate:
                union = ImageChops.lighter(union, candidate)
        encoded = io.BytesIO()
        union.save(encoded, format="PNG")
        binding = context.store.persist_structured(
            StructuredValue(
                "quality_evidence",
                "TextMaskUnion",
                "1.0",
                {
                    "image": {"artifact_id": image.artifact_id},
                    "bundle": {"artifact_id": bundle.artifact_id},
                    "source_run_id": context.run_id,
                    "node_id": context.node_id,
                    "policy": "all_candidates_union@1",
                },
            )
        )
        mask = context.store.persist_bytes(
            encoded.getvalue(),
            kind="binary_mask",
            schema_name="png",
            schema_version="1.0",
            identity_metadata={
                "media_type": "image/png",
                "selection_binding": {"artifact_id": binding.artifact_id},
            },
        )
        return NodeExecutionResult({"candidates": bundle, "mask": mask})


class SelectTextMaskAdapter:
    spec = AdapterSpec(
        "select_text_mask",
        "1",
        ("select_text_mask@1",),
        parameter_schema={
            "type": "object",
            "properties": {"candidate_index": {"type": "integer", "minimum": -1, "maximum": 63}},
        },
        defaults={"candidate_index": -1},
    )

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        ref = context.inputs.get("candidates")
        if not isinstance(ref, ArtifactRef) or not context.store.verify_digest(ref):
            raise ContractError("candidate evidence missing")
        raw = context.store.read_structured(ref)
        items = raw["candidates"]
        index = context.parameters["candidate_index"]
        if index == -1:
            if len(items) != 1:
                raise ContractError(
                    f"expected exactly one mask, found {len(items)}; "
                    "explicitly select candidate_index"
                )
            index = 0
        if not 0 <= index < len(items):
            raise ContractError("candidate index out of range")
        mask = ArtifactRef(**items[index]["mask"])
        image = ArtifactRef(**raw["image"])
        if not context.store.verify_digest(mask) or not context.store.verify_digest(image):
            raise ContractError("candidate inputs missing")
        validate_mask(
            context.store.blob_path(mask).read_bytes(),
            image_size(context.store.blob_path(image).read_bytes()),
        )
        return NodeExecutionResult({"mask": mask})

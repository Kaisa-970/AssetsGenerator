"""RGB + mask remote shape boundary; no model-specific branch in DAG execution."""

import io
from collections.abc import Mapping
from typing import Any

from PIL import Image

from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .dag_remote_adapter import RemoteNodeAdapter
from .mask_binding import validate_mask_image_binding
from .models import ArtifactRef, PortValue
from .remote_http import RemoteJobClient
from .remote_protocol import RemoteIdentity, RemoteJob, RemoteOutput, decode_remote_json
from .remote_shape_output import import_shape_output
from .sam3d_http import validate_images, validate_options
from .serialization import canonical_json_bytes, sha256_bytes


class RemoteMaskedShapeAdapter(RemoteNodeAdapter):
    def __init__(self, endpoint: str, identity: RemoteIdentity, upstream_digest: str):
        RemoteIdentity("upstream", upstream_digest)
        fixed = {
            "remote_endpoint": RemoteJobClient(endpoint).endpoint,
            "service_id": identity.service_id,
            "backend_digest": identity.backend_digest,
            "upstream_digest": upstream_digest,
        }
        self._spec = AdapterSpec(
            "remote_masked_shape",
            "1",
            ("masked_shape_generation@1",),
            execution_kind="remote",
            parameter_schema={
                "type": "object",
                "properties": {
                    **{key: {"type": "string", "enum": [value]} for key, value in fixed.items()},
                    "seed": {"type": "integer", "minimum": 0, "maximum": 2**32 - 1},
                    "steps1": {"type": "integer", "minimum": 1, "maximum": 100},
                    "steps2": {"type": "integer", "minimum": 1, "maximum": 100},
                    "cfg1": {"type": "number", "minimum": 0, "maximum": 15},
                    "cfg2": {"type": "number", "minimum": 0, "maximum": 15},
                    "bake": {"type": "boolean"},
                    "bake_view_resolution": {"type": "integer", "enum": [512, 1024]},
                    "bake_filter": {"type": "string", "enum": ["mipmap", "legacy"]},
                    "texture": {"type": "integer", "enum": [512, 1024, 2048]},
                    "reduction": {"type": "number", "minimum": 0, "maximum": 0.95},
                },
            },
            defaults={**fixed, **validate_options({})},
        )

    @property
    def spec(self) -> AdapterSpec:
        return self._spec

    def input_blobs(self, context: NodeExecutionContext) -> Mapping[str, ArtifactRef]:
        refs = {}
        for name, kind in (("image", "rgb_image"), ("mask", "binary_mask")):
            ref = context.inputs.get(name)
            if not isinstance(ref, ArtifactRef) or not context.store.verify_digest(ref):
                raise ContractError("masked shape requires intact image/mask references")
            if context.store.get_manifest(ref.artifact_id).identity.kind != kind:
                raise ContractError("masked shape input kind mismatch")
            refs[name] = ref
        validate_mask_image_binding(context.store, refs["image"], refs["mask"])
        validate_images(
            *(context.store.blob_path(refs[name]).read_bytes() for name in ("image", "mask"))
        )
        return refs

    def prepare_payload(self, context: NodeExecutionContext) -> dict[str, Any]:
        refs = self.input_blobs(context)
        return {
            "operation": "masked_shape_generation@1",
            **{
                name + "_digest": context.store.get_manifest(ref.artifact_id).identity.blob_digest
                for name, ref in refs.items()
            },
            "parameters": validate_options(
                {
                    name: context.parameters[name]
                    for name in (*validate_options({}), "bake_view_resolution", "bake_filter")
                    if name in context.parameters
                }
            ),
            "backend_digest": context.parameters["upstream_digest"],
        }

    def import_result(
        self, context: NodeExecutionContext, job: RemoteJob, blobs: Mapping[str, bytes]
    ) -> NodeExecutionResult:
        media = {
            "mesh": "model/gltf-binary",
            "shape_metadata": "application/json",
            "sam3d_evidence": "application/json",
            "actual_mask": "image/png",
        }
        if set(blobs) != set(media):
            raise ContractError("masked shape requires complete boundary evidence")
        for name, expected in media.items():
            if RemoteOutput.from_job(job, name).media_type != expected:
                raise ContractError("masked shape output media mismatch")
        payload = self.prepare_payload(context)
        evidence = decode_remote_json(blobs["sam3d_evidence"])
        expected_request = {
            "schema": "sam3d-request@1",
            "image": payload["image_digest"],
            "mask": payload["mask_digest"],
            "points": None,
            "options": payload["parameters"],
        }
        if (
            not isinstance(evidence, dict)
            or evidence.get("schema") != "sam3d-result@1"
            or evidence.get("request") != expected_request
            or evidence.get("request_digest")
            != sha256_bytes(canonical_json_bytes(expected_request))
            or evidence.get("deployment", {}).get("backend_digest") != payload["backend_digest"]
        ):
            raise ContractError("masked shape evidence belongs to another request/backend")
        for name, source in (("model.glb", "mesh"), ("mask.png", "actual_mask")):
            data = blobs[source]
            if evidence.get("files", {}).get(name) != {
                "sha256": sha256_bytes(data),
                "byte_length": len(data),
            }:
                raise ContractError("masked shape evidence file mismatch")
        refs = self.input_blobs(context)
        with (
            Image.open(io.BytesIO(blobs["actual_mask"])) as actual,
            Image.open(context.store.blob_path(refs["mask"])) as expected_mask,
        ):
            if (
                actual.format != "PNG"
                or actual.mode not in {"1", "L"}
                or actual.size != expected_mask.size
                or actual.convert("L").tobytes() != expected_mask.convert("L").tobytes()
            ):
                raise ContractError("masked shape actual mask differs from input")
        output = import_shape_output(
            context.store, {key: blobs[key] for key in ("mesh", "shape_metadata")}
        )
        if output.backend_metadata.get("boundary_evidence") != evidence:
            raise ContractError("masked shape metadata evidence mismatch")
        # Keep exact evidence as explicit node outputs, independently addressable in provenance.
        result: dict[str, PortValue | list[PortValue]] = {
            "mesh": output.mesh,
            "material": output.material,
            "native_frame": output.native_frame,
        }
        for name, kind, schema in (
            ("sam3d_evidence", "remote_job_result", "Sam3DResult"),
            ("actual_mask", "binary_mask", "png"),
        ):
            result[name] = context.store.persist_bytes(
                blobs[name],
                kind=kind,
                schema_name=schema,
                schema_version="1.0",
                identity_metadata={"media_type": media[name]},
            )
        return NodeExecutionResult(result)

"""Executors for discovered model-service capabilities.

The generic adapter deliberately knows nothing about a model's operator name.  A
validated descriptor supplies the port contracts and the remote-jobs transport
does the execution; specialised adapters below remain for legacy wire profiles.
"""

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from .compiled_plan import thaw
from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .dag_remote_adapter import RemoteNodeAdapter
from .dag_text_segmentation import RemoteTextInputSegmentationAdapter
from .model_service_descriptor import (
    RESERVED_PARAMETERS,
    DiscoveredShapeAdapter,
    capability_digest,
    dynamic_operator_spec,
    select_capability,
    validate_descriptor,
)
from .models import ArtifactRef, StructuredValue
from .remote_http import RemoteJobClient
from .remote_protocol import RemoteIdentity, RemoteJob, RemoteOutput
from .serialization import sha256_bytes, to_primitive


class GenericRemoteCapabilityAdapter(RemoteNodeAdapter):
    """Run any descriptor capability with a validated Artifact contract.

    No local Operator or model-specific protocol is required.  The descriptor's
    dynamic ports are converted into a constrained ``OperatorSpec`` and output
    bytes are imported only after the transport digest, media type and declared
    kind/schema have all been checked.
    """

    def __init__(self, endpoint: str, descriptor: object, capability_id: str | None = None):
        checked = validate_descriptor(descriptor)
        selected = select_capability(checked, capability_id)
        if selected.get("transport") != "remote_jobs@1":
            raise ContractError("generic capability requires remote_jobs@1 transport")
        if "inputs" not in selected or "outputs" not in selected:
            raise ContractError("generic capability must declare inputs and outputs")
        self.endpoint = RemoteJobClient(endpoint).endpoint
        self.descriptor = checked
        self.capability = selected
        self.capability_id = selected["capability_id"]
        self.capability_digest = capability_digest(checked, self.capability_id)
        self._ports = {"inputs": selected["inputs"], "outputs": selected["outputs"]}
        operator = dynamic_operator_spec(selected).name
        self._spec = AdapterSpec(
            "generic_remote_capability",
            "1",
            (f"{operator}@1",),
            execution_kind="remote",
            parameter_schema={
                **selected["parameter_schema"],
                "properties": {
                    **selected["parameter_schema"].get("properties", {}),
                    "remote_endpoint": {"type": "string", "enum": [self.endpoint]},
                    "service_id": {"type": "string", "enum": [checked["service_id"]]},
                    "backend_digest": {"type": "string", "enum": [checked["backend_digest"]]},
                },
            },
            defaults={
                **selected["defaults"],
                "remote_endpoint": self.endpoint,
                "service_id": checked["service_id"],
                "backend_digest": checked["backend_digest"],
            },
        )

    @property
    def spec(self) -> AdapterSpec:
        return self._spec

    def input_blobs(self, context: NodeExecutionContext) -> Mapping[str, ArtifactRef]:
        uploads: dict[str, ArtifactRef] = {}
        for name, _port in self._ports["inputs"].items():
            value = context.inputs.get(name)
            if isinstance(value, ArtifactRef):
                identity = context.store.get_manifest(value.artifact_id).identity
                expected_media = self._ports["inputs"][name].get("media_type")
                if (
                    expected_media is not None
                    and identity.identity_metadata.get("media_type") != expected_media
                ):
                    raise ContractError(f"generic input {name} media type mismatch")
            values = value if isinstance(value, list) else [value]
            for index, item in enumerate(values):
                if isinstance(item, ArtifactRef):
                    key = name if not isinstance(value, list) else f"{name}[{index}]"
                    uploads[key] = item
        return uploads

    def prepare_payload(self, context: NodeExecutionContext) -> dict[str, Any]:
        parameters = self.spec.normalize_parameters(context.parameters)
        inputs: dict[str, Any] = {}
        for name, value in context.inputs.items():
            if isinstance(value, list):
                inputs[name] = [
                    {"artifact_id": item.artifact_id}
                    if isinstance(item, ArtifactRef)
                    else to_primitive(item)
                    for item in value
                ]
            elif isinstance(value, ArtifactRef):
                inputs[name] = {"artifact_id": value.artifact_id}
            else:
                inputs[name] = to_primitive(value)
        return {
            "capability_id": self.capability_id,
            "operation": self.capability.get("operator", f"remote_{self.capability_id}"),
            "inputs": inputs,
            "parameters": {
                key: value for key, value in parameters.items() if key not in RESERVED_PARAMETERS
            },
        }

    def import_result(
        self, context: NodeExecutionContext, job: RemoteJob, blobs: Mapping[str, bytes]
    ) -> NodeExecutionResult:
        declared = self._ports["outputs"]
        expected_names = set(declared)
        if set(blobs) != expected_names:
            raise ContractError("generic remote result outputs differ from descriptor")
        outputs: dict[str, Any] = {}
        for name, contract in declared.items():
            descriptor = RemoteOutput.from_job(job, name)
            data = blobs[name]
            if descriptor.byte_length != len(data) or descriptor.blob_digest != sha256_bytes(data):
                raise ContractError(f"generic output {name} transport digest mismatch")
            media_type = contract.get("media_type")
            if media_type is not None and descriptor.media_type != media_type:
                raise ContractError(f"generic output {name} media type mismatch")
            kind = contract["kinds"][0]
            schema_name = contract["schema_name"]
            schema_version = contract["schema_version"]
            carrier = contract["carriers"][0]
            metadata = {
                key: contract[key]
                for key in ("frame_id", "up_axis", "unit", "media_type")
                if key in contract
            }
            if kind in {"rgb_image", "rgba_image", "binary_mask"}:
                from .generic_artifact_content import image_metadata

                metadata.update(
                    image_metadata(data, kind, schema_name, schema_version, descriptor.media_type)
                )
            elif kind == "text" and schema_name == "plain_text":
                if schema_version != "1.0" or descriptor.media_type != "text/plain":
                    raise ContractError("generic text requires plain_text@1.0 and text/plain")
                try:
                    data.decode("utf-8", errors="strict")
                except UnicodeDecodeError as error:
                    raise ContractError("generic plain text requires UTF-8") from error
            elif kind in {"triangle_mesh", "collision_mesh"}:
                if (schema_name, schema_version, descriptor.media_type) != (
                    "glTF",
                    "2.0",
                    "model/gltf-binary",
                ):
                    raise ContractError("generic mesh requires glTF@2.0 and model/gltf-binary")
                from .remote_shape_output import validate_self_contained_glb

                try:
                    validate_self_contained_glb(data)
                except (ValueError, TypeError) as error:
                    raise ContractError(
                        f"generic output {name} is not a valid self-contained GLB"
                    ) from error
            elif kind not in {"text", "rgb_image", "rgba_image", "binary_mask"}:
                raise ContractError(
                    f"generic output {name} kind {kind} has no registered content validator"
                )
            if carrier == "structured":
                try:
                    value = json.loads(data)
                except (TypeError, ValueError) as error:
                    raise ContractError(f"generic output {name} is not JSON") from error
                if not isinstance(value, dict):
                    raise ContractError(f"generic structured output {name} must be an object")
                outputs[name] = StructuredValue(kind, schema_name, schema_version, value)
            else:
                outputs[name] = context.store.persist_bytes(
                    data,
                    kind=kind,
                    schema_name=schema_name,
                    schema_version=schema_version,
                    identity_metadata=metadata,
                )
        return NodeExecutionResult(outputs)


class DiscoveredTextSegmentationAdapter(RemoteTextInputSegmentationAdapter):
    """Pinned SAM3 text wire profile, not a generic capability routing protocol.

    The existing wire receives prompt/confidence and no capability_id. Multiple
    advertised profiles therefore select defaults for this same implementation,
    not different remote models. Their Backend identities remain independent.
    """

    def __init__(self, endpoint: str, descriptor: object, capability_id: str | None = None):
        checked = validate_descriptor(descriptor)
        selected = select_capability(checked, capability_id)
        if (selected["operator"], selected["transport"]) != (
            "text_segmentation@2",
            "sam3_text_jobs@1",
        ):
            raise ContractError("text segmentation requires text_segmentation@2 / sam3_text_jobs@1")
        super().__init__(endpoint, RemoteIdentity(checked["service_id"], checked["backend_digest"]))
        expected = {
            key: thaw(value)
            for key, value in self.spec.parameter_schema["properties"].items()
            if key not in RESERVED_PARAMETERS
        }
        schema = selected["parameter_schema"]
        if (
            set(schema) - {"type", "properties", "additionalProperties", "required"}
            or schema.get("properties") != expected
            or schema.get("required", []) not in ([], ["confidence"])
            or set(selected["defaults"]) != {"confidence"}
        ):
            raise ContractError(
                "SAM3 text capability must use the fixed confidence parameter contract"
            )
        # Validate defaults with Core's established schema before publishing them.
        defaults = self.spec.normalize_parameters(selected["defaults"])
        self._spec = replace(
            self.spec,
            name="discovered_text_segmentation",
            defaults=defaults,
        )
        self.capability_id = selected["capability_id"]
        self.capability_digest = capability_digest(checked, self.capability_id)


def discovered_service_adapter(
    endpoint: str, descriptor: object, capability_id: str | None = None
) -> RemoteNodeAdapter:
    """Choose only an explicitly supported Operator/wire pair."""
    checked: dict[str, Any] = validate_descriptor(descriptor)
    selected = select_capability(checked, capability_id)
    if "inputs" in selected and "outputs" in selected:
        return GenericRemoteCapabilityAdapter(endpoint, checked, capability_id)
    if selected["operator"] == "shape_generation@1":
        return DiscoveredShapeAdapter(endpoint, checked, capability_id)
    if selected["operator"] == "text_segmentation@2":
        return DiscoveredTextSegmentationAdapter(endpoint, checked, capability_id)
    raise ContractError(
        f"model service capability {selected['operator']!r} has no executable adapter"
    )

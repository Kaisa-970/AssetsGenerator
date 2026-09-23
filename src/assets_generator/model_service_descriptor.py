"""Closed service discovery for the existing image-to-mesh Operator contract.

Service descriptions are data, never executable adapter or port definitions.
The OperatorSpec remains authoritative for all inputs and outputs.
"""

from __future__ import annotations

from typing import Any

from .compiled_plan import thaw
from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext
from .dag_remote_shape import RemoteShapeAdapter
from .models import BackendNativeFrame
from .remote_http import RemoteJobClient
from .remote_protocol import RemoteIdentity, decode_remote_json
from .serialization import canonical_json_bytes, sha256_bytes
from .spatial import validate_backend_native_frame

RESERVED_PARAMETERS = frozenset({"remote_endpoint", "service_id", "backend_digest"})
_REQUIRED_FIELDS = {
    "schema_version",
    "display_name",
    "service_id",
    "backend_digest",
    "operator",
    "transport",
    "parameter_schema",
    "defaults",
}
_OPTIONAL_METADATA = {"frame_id", "up_axis", "unit"}


def validate_descriptor(raw: object) -> dict[str, Any]:
    """Validate and copy JSON primitives; accept only the supported shape boundary."""
    if (
        not isinstance(raw, dict)
        or not _REQUIRED_FIELDS.issubset(raw)
        or set(raw) - (_REQUIRED_FIELDS | _OPTIONAL_METADATA)
    ):
        raise ContractError("model service descriptor has missing or unknown fields")
    raw = {**raw, **{field: "unknown" for field in _OPTIONAL_METADATA if field not in raw}}
    if (
        raw["schema_version"] != "model_service@1"
        or raw["operator"] != "shape_generation@1"
        or raw["transport"] != "remote_jobs@1"
    ):
        raise ContractError("unsupported model service schema, Operator or transport")
    name = raw["display_name"]
    if not isinstance(name, str) or not name.strip() or len(name) > 128:
        raise ContractError("model service display_name must contain 1..128 characters")
    if any(ord(char) < 32 for char in name):
        raise ContractError("model service display_name contains control characters")
    RemoteIdentity(raw["service_id"], raw["backend_digest"])
    for field in ("frame_id", "up_axis", "unit"):
        value = raw[field]
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise ContractError(f"model service {field} must contain 1..128 characters")
    encoded = canonical_json_bytes(raw)
    if len(encoded) > 64 * 1024:
        raise ContractError("model service descriptor exceeds size limit")
    value = decode_remote_json(encoded)
    schema = value["parameter_schema"]
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ContractError("model service parameters must describe an object")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict) or len(properties) > 64:
        raise ContractError("model service allows at most 64 parameters")
    if schema.get("additionalProperties", False) is not False:
        raise ContractError("model service parameters must be closed")
    if RESERVED_PARAMETERS.intersection(properties):
        raise ContractError("model service cannot declare reserved transport parameters")
    if any(not key or len(key) > 128 for key in properties):
        raise ContractError("model service parameter names must contain 1..128 characters")
    AdapterSpec(
        "discovered_shape",
        "1",
        ("shape_generation@1",),
        execution_kind="remote",
        parameter_schema=schema,
        defaults=value["defaults"],
    )
    return value  # type: ignore[no-any-return]


def detect_service(endpoint: str) -> dict[str, Any]:
    """Read only; bounded GET, no redirects, uploads, queue claims or inference."""
    client = RemoteJobClient(endpoint, max_response_bytes=64 * 1024)
    descriptor = validate_descriptor(client.service_descriptor())
    return {
        "endpoint": client.endpoint,
        "descriptor": descriptor,
        "descriptor_digest": sha256_bytes(canonical_json_bytes(descriptor)),
    }


class DiscoveredShapeAdapter(RemoteShapeAdapter):
    """Reuse verified RGBA/GLB import while forwarding declared model parameters."""

    def __init__(self, endpoint: str, descriptor: object):
        checked = validate_descriptor(descriptor)
        identity = RemoteIdentity(checked["service_id"], checked["backend_digest"])
        super().__init__(endpoint, identity)
        fixed = {key: thaw(self.spec.defaults[key]) for key in RESERVED_PARAMETERS}
        schema = checked["parameter_schema"]
        self._spec = AdapterSpec(
            "discovered_shape",
            "1",
            ("shape_generation@1",),
            execution_kind="remote",
            parameter_schema={
                **schema,
                "properties": {
                    **schema.get("properties", {}),
                    **{key: {"type": "string", "enum": [value]} for key, value in fixed.items()},
                },
            },
            defaults={**checked["defaults"], **fixed},
        )

    def prepare_payload(self, context: NodeExecutionContext) -> dict[str, Any]:
        parameters = self.spec.normalize_parameters(context.parameters)
        return {
            "operation": "shape_generation@1",
            "parameters": {
                key: thaw(value)
                for key, value in parameters.items()
                if key not in RESERVED_PARAMETERS
            },
        }


def shape_service_descriptor(
    identity: RemoteIdentity,
    display_name: str,
    *,
    frame: BackendNativeFrame | None = None,
    fixed_parameters: bool = False,
) -> dict[str, Any]:
    """Describe a verified shape deployment; fix ineffective legacy parameters.

    Omitting frame preserves discovery compatibility for older callers. Fixed
    seed/pipeline_type retain the handler's wire contract without offering knobs
    that a deployment (such as TripoSR) does not implement.
    """
    if frame is not None:
        validate_backend_native_frame(frame)
    spec = RemoteShapeAdapter("http://127.0.0.1", identity).spec
    properties = {
        key: thaw(value)
        for key, value in spec.parameter_schema["properties"].items()
        if key not in RESERVED_PARAMETERS
    }
    if fixed_parameters:
        for key, value in properties.items():
            value["enum"] = [thaw(spec.defaults[key])]
    return validate_descriptor(
        {
            "schema_version": "model_service@1",
            "display_name": display_name,
            "service_id": identity.service_id,
            "backend_digest": identity.backend_digest,
            "operator": "shape_generation@1",
            "transport": "remote_jobs@1",
            "frame_id": frame.frame_id if frame is not None else "unknown",
            "up_axis": frame.up_axis if frame is not None else "unknown",
            "unit": frame.unit if frame is not None else "unknown",
            "parameter_schema": {
                "type": "object",
                "properties": properties,
            },
            "defaults": {
                key: thaw(value)
                for key, value in spec.defaults.items()
                if key not in RESERVED_PARAMETERS
            },
        }
    )

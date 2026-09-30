"""Validate service declarations and materialize constrained dynamic contracts.

Descriptors are data, never executable code. Validated declarations become an
OperatorSpec, the authority for ports and locally implemented input relations.
"""

from __future__ import annotations

from typing import Any

from .compiled_plan import thaw
from .contracts import ContractError, OperatorSpec, PortSpec, RelationSpec
from .dag_adapters import AdapterSpec, NodeExecutionContext
from .models import ARTIFACT_KINDS, STRUCTURED_KINDS

try:
    from .dag_remote_shape import RemoteShapeAdapter
except ModuleNotFoundError as exc:
    if exc.name not in {"trimesh", "pygltflib"}:
        raise

    class RemoteShapeAdapter:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ContractError("shape service adapter requires mesh runtime dependencies")


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
_CAPABILITY_FIELDS = {
    "capability_id",
    "display_name",
    "operator",
    "transport",
    "parameter_schema",
    "defaults",
    "frame_id",
    "up_axis",
    "unit",
    "inputs",
    "outputs",
    "request_protocol",
    "result_protocol",
    "relations",
}

_PORT_FIELDS = {
    "kind",
    "kinds",
    "carrier",
    "carriers",
    "schema_name",
    "schema_version",
    "cardinality",
    "requires_frame",
    "requires_unit",
    "frame_id",
    "up_axis",
    "unit",
    "media_type",
}
_CARDINALITIES = {"one", "zero_or_one", "one_or_more", "zero_or_more", "many"}
_CARRIERS = {"artifact_ref", "structured"}
_SPATIAL_KINDS = {
    "triangle_mesh",
    "collision_mesh",
    "point_cloud",
    "gaussian_splat",
    "depth_map",
    "camera_collection",
}


def _validate_port_contracts(value: object, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or not value:
        raise ContractError(f"model service capability {label} must be a nonempty object")
    result: dict[str, dict[str, Any]] = {}
    for name, raw in value.items():
        if not isinstance(name, str) or not name or len(name) > 128:
            raise ContractError(f"model service {label} port name is invalid")
        if not isinstance(raw, dict) or set(raw) - _PORT_FIELDS:
            raise ContractError(f"model service {label}.{name} has unknown fields")
        kinds = raw.get("kinds")
        if kinds is None:
            kinds = [raw.get("kind")]
        if (
            not isinstance(kinds, list)
            or not kinds
            or any(
                not isinstance(kind, str) or kind not in (ARTIFACT_KINDS | STRUCTURED_KINDS)
                for kind in kinds
            )
        ):
            raise ContractError(f"model service {label}.{name} has invalid kind")
        carriers = raw.get("carriers")
        if carriers is None:
            carriers = [raw.get("carrier", "artifact_ref")]
        if (
            not isinstance(carriers, list)
            or not carriers
            or any(carrier not in _CARRIERS for carrier in carriers)
        ):
            raise ContractError(f"model service {label}.{name} has invalid carrier")
        cardinality = raw.get("cardinality", "one")
        if cardinality not in _CARDINALITIES:
            raise ContractError(f"model service {label}.{name} has invalid cardinality")
        schema_name = raw.get("schema_name")
        schema_version = raw.get("schema_version")
        if not isinstance(schema_name, str) or not schema_name.strip():
            raise ContractError(f"model service {label}.{name} requires schema_name")
        if not isinstance(schema_version, str) or not schema_version.strip():
            raise ContractError(f"model service {label}.{name} requires schema_version")
        media_type = raw.get("media_type")
        if "artifact_ref" in carriers and media_type is None:
            raise ContractError(f"model service {label}.{name} requires media_type")
        if media_type is not None and (
            not isinstance(media_type, str)
            or not media_type.strip()
            or "/" not in media_type
            or any(ord(char) < 33 or ord(char) > 126 for char in media_type)
        ):
            raise ContractError(f"model service {label}.{name} has invalid media_type")
        if "text" in kinds:
            if (schema_name, schema_version) != ("plain_text", "1.0"):
                raise ContractError(f"model service {label}.{name} text requires plain_text@1.0")
            if "artifact_ref" in carriers and media_type != "text/plain":
                raise ContractError(
                    f"model service {label}.{name} text requires text/plain media_type"
                )
        spatial = any(kind in _SPATIAL_KINDS for kind in kinds)
        requires_frame = bool(raw.get("requires_frame", spatial))
        requires_unit = bool(raw.get("requires_unit", spatial))
        if (
            type(raw.get("requires_frame", requires_frame)) is not bool
            or type(raw.get("requires_unit", requires_unit)) is not bool
        ):
            raise ContractError(f"model service {label}.{name} frame/unit flags are invalid")
        if spatial and (not raw.get("frame_id") or not raw.get("unit")):
            raise ContractError(f"model service {label}.{name} requires frame_id and unit")
        normalized = {
            "kinds": list(dict.fromkeys(kinds)),
            "carriers": list(dict.fromkeys(carriers)),
            "schema_name": schema_name,
            "schema_version": schema_version,
            "cardinality": cardinality,
            "requires_frame": requires_frame,
            "requires_unit": requires_unit,
        }
        for field in ("frame_id", "up_axis", "unit", "media_type"):
            if field in raw:
                if (
                    not isinstance(raw[field], str)
                    or not raw[field].strip()
                    or len(raw[field]) > 128
                ):
                    raise ContractError(f"model service {label}.{name} {field} is invalid")
                normalized[field] = raw[field]
        result[name] = normalized
    return result


def _validate_capability(value: object, index: int) -> dict[str, Any]:
    """Validate advertised contracts independently of model or Operator names."""
    if not isinstance(value, dict):
        raise ContractError(f"model service capability {index} must be an object")
    unknown = set(value) - _CAPABILITY_FIELDS
    if unknown:
        raise ContractError(f"model service capability has unknown fields: {sorted(unknown)}")
    required = {"transport", "parameter_schema", "defaults"}
    if not required.issubset(value):
        raise ContractError("model service capability has missing fields")
    operator = value.get("operator")
    transport = value["transport"]
    if operator is not None and (
        not isinstance(operator, str) or not operator.strip() or len(operator) > 128
    ):
        raise ContractError("model service capability operator is invalid")
    if not isinstance(transport, str) or not transport.strip() or len(transport) > 64:
        raise ContractError("model service capability transport is invalid")
    capability_id = value.get("capability_id", operator)
    if not isinstance(capability_id, str) or not capability_id.strip() or len(capability_id) > 128:
        raise ContractError("model service capability_id is invalid")
    if operator is None:
        operator = dynamic_operator_key({"capability_id": capability_id})
    if "display_name" in value and (
        not isinstance(value["display_name"], str)
        or not value["display_name"].strip()
        or len(value["display_name"]) > 128
    ):
        raise ContractError("model service capability display_name is invalid")
    schema = value["parameter_schema"]
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ContractError("model service capability parameters must describe an object")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict) or len(properties) > 64:
        raise ContractError("model service capability allows at most 64 parameters")
    if schema.get("additionalProperties", False) is not False:
        raise ContractError("model service capability parameters must be closed")
    if not isinstance(value["defaults"], dict) or set(value["defaults"]) - set(properties):
        raise ContractError("model service capability defaults must be declared parameters")
    if RESERVED_PARAMETERS.intersection(properties):
        raise ContractError("model service cannot declare reserved transport parameters")
    has_ports = "inputs" in value or "outputs" in value
    if has_ports and not {"inputs", "outputs"}.issubset(value):
        raise ContractError("dynamic capability must declare both inputs and outputs")
    if has_ports:
        inputs = _validate_port_contracts(value["inputs"], "inputs")
        outputs = _validate_port_contracts(value["outputs"], "outputs")
        if set(inputs) & set(outputs):
            raise ContractError("dynamic capability input/output port names must be disjoint")
        if value.get("request_protocol", "remote_jobs@1") != "remote_jobs@1":
            raise ContractError("unsupported dynamic capability request protocol")
        if value.get("result_protocol", "remote_jobs@1") != "remote_jobs@1":
            raise ContractError("unsupported dynamic capability result protocol")
        for label, ports in (("inputs", inputs), ("outputs", outputs)):
            for name, port in ports.items():
                if len(port["kinds"]) != 1 or len(port["carriers"]) != 1:
                    raise ContractError(
                        f"dynamic capability {label}.{name} must declare one kind and carrier"
                    )
    for field in ("frame_id", "up_axis", "unit"):
        if field in value:
            metadata = value[field]
            if not isinstance(metadata, str) or not metadata.strip() or len(metadata) > 128:
                raise ContractError(f"model service capability {field} is invalid")
    normalized = {"capability_id": capability_id, "operator": operator, **dict(value)}
    if "relations" in value and not has_ports:
        raise ContractError("capability relations require dynamic ports")
    if has_ports:
        normalized["inputs"] = inputs
        normalized["outputs"] = outputs
        normalized.setdefault("request_protocol", "remote_jobs@1")
        normalized.setdefault("result_protocol", "remote_jobs@1")
        if "relations" in value:
            raw_relations = value["relations"]
            if not isinstance(raw_relations, list) or len(raw_relations) > 32:
                raise ContractError("capability relations must be a bounded list")
            for relation in raw_relations:
                if not isinstance(relation, dict) or set(relation) != {"validator", "inputs"}:
                    raise ContractError("capability relation requires validator and inputs")
                RelationSpec(relation["validator"], relation["inputs"])
            # Declarations only reference locally implemented validators; they
            # never install executable validation code from the service.
            from .relations import default_relation_registry

            default_relation_registry().validate_static(
                dynamic_operator_spec(normalized),
                {name: f"pipeline.inputs.{name}" for name in inputs},
            )
        normalized["operator"] = dynamic_operator_key(normalized)
    return normalized


def dynamic_operator_spec(capability: dict[str, Any]) -> OperatorSpec:
    """Build a constrained OperatorSpec for a capability with declared ports."""
    if "inputs" not in capability or "outputs" not in capability:
        raise ContractError("capability does not declare dynamic ports")

    def port(raw: dict[str, Any]) -> PortSpec:
        return PortSpec(
            tuple(raw["kinds"]),
            raw["cardinality"],
            tuple(raw["carriers"]),
            raw["schema_name"],
            raw["schema_version"],
            raw["requires_frame"],
            raw["requires_unit"],
            False,
            raw.get("media_type"),
        )

    # Operator identity is capability scoped. Backend identity remains in the
    # node binding, so the same capability can be provided by multiple services.
    operator = dynamic_operator_key(capability)
    return OperatorSpec(
        name=operator,
        version="1",
        inputs={name: port(raw) for name, raw in capability["inputs"].items()},
        outputs={name: port(raw) for name, raw in capability["outputs"].items()},
        relations=tuple(
            RelationSpec(item["validator"], item["inputs"])
            for item in capability.get("relations", [])
        ),
    )


def dynamic_operator_key(capability: dict[str, Any]) -> str:
    """Return a stable, filesystem/UI-safe operator name for one capability."""
    capability_id = capability.get("capability_id")
    if not isinstance(capability_id, str) or not capability_id:
        raise ContractError("dynamic capability requires capability_id")
    identity = dict(capability)
    identity.pop("operator", None)
    suffix = sha256_bytes(canonical_json_bytes(identity)).split(":", 1)[1][:24]
    return f"remote_capability_{suffix}"


def validate_descriptor(raw: object) -> dict[str, Any]:
    """Validate a service descriptor and normalize capability advertisements.

    ``model_service@1`` originally exposed one flat capability.  The flat form
    remains accepted byte-for-byte.  A descriptor may now instead provide a
    ``capabilities`` list; the first capability is mirrored into the legacy
    fields so existing registry and UI code can continue to consume known
    ``shape_generation@1`` services. Dynamic port declarations materialize an
    OperatorSpec and execute through the generic remote adapter.
    """
    if isinstance(raw, dict) and "capabilities" in raw:
        base_fields = {
            "schema_version",
            "display_name",
            "service_id",
            "backend_digest",
            "capabilities",
            # Normalized descriptors persisted by the editor also retain the
            # mirrored legacy fields for old readers.
            "operator",
            "transport",
            "parameter_schema",
            "defaults",
        }
        if set(raw) - (base_fields | _OPTIONAL_METADATA):
            raise ContractError("model service descriptor has unknown fields")
        capabilities = raw["capabilities"]
        if not isinstance(capabilities, list) or not capabilities or len(capabilities) > 32:
            raise ContractError("model service capabilities must contain 1..32 items")
        checked_caps = [
            _validate_capability(item, index) for index, item in enumerate(capabilities)
        ]
        capability_ids = [item["capability_id"] for item in checked_caps]
        if len(set(capability_ids)) != len(capability_ids):
            raise ContractError("model service capability_id values must be unique")
        first = checked_caps[0]
        raw = {
            **raw,
            "operator": first["operator"],
            "transport": first["transport"],
            "parameter_schema": first["parameter_schema"],
            "defaults": first["defaults"],
            "capabilities": checked_caps,
            **{field: raw.get(field, first.get(field, "unknown")) for field in _OPTIONAL_METADATA},
        }

    if (
        not isinstance(raw, dict)
        or not _REQUIRED_FIELDS.issubset(raw)
        or set(raw) - (_REQUIRED_FIELDS | _OPTIONAL_METADATA | {"capabilities"})
    ):
        raise ContractError("model service descriptor has missing or unknown fields")
    raw = {**raw, **{field: "unknown" for field in _OPTIONAL_METADATA if field not in raw}}
    if raw["schema_version"] != "model_service@1":
        raise ContractError("unsupported model service schema")
    if "capabilities" in raw:
        # The mirrored fields are used by legacy callers. They must still be a
        # valid capability, even when the descriptor also advertises unknowns.
        _validate_capability({key: raw[key] for key in _CAPABILITY_FIELDS if key in raw}, 0)
    elif raw["operator"] != "shape_generation@1" or raw["transport"] != "remote_jobs@1":
        raise ContractError("unsupported model service Operator or transport")
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
    if raw["operator"] == "shape_generation@1":
        AdapterSpec(
            "discovered_shape",
            "1",
            ("shape_generation@1",),
            execution_kind="remote",
            parameter_schema=schema,
            defaults=value["defaults"],
        )
    return value  # type: ignore[no-any-return]


def select_capability(
    descriptor: dict[str, Any], capability_id: str | None = None
) -> dict[str, Any]:
    """Return one normalized capability from a validated descriptor.

    Legacy flat descriptors expose a single capability whose identity is the
    operator name. Multi-capability descriptors must be selected explicitly;
    silently using the first capability would make Backend identity ambiguous.
    """
    capabilities = descriptor.get("capabilities")
    if not capabilities:
        selected = {key: descriptor[key] for key in _CAPABILITY_FIELDS if key in descriptor}
        selected.setdefault("capability_id", descriptor["operator"])
        return selected
    if capability_id is None:
        raise ContractError("model service capability_id is required")
    for item in capabilities:
        if item["capability_id"] == capability_id:
            return dict(item)
    raise ContractError(f"unknown model service capability_id {capability_id!r}")


def capability_digest(descriptor: dict[str, Any], capability_id: str | None = None) -> str:
    """Digest the selected capability, independent of sibling capabilities."""
    return sha256_bytes(canonical_json_bytes(select_capability(descriptor, capability_id)))


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
    """Remote adapter bound to one explicitly selected service capability."""

    def __init__(self, endpoint: str, descriptor: object, capability_id: str | None = None):
        checked = validate_descriptor(descriptor)
        selected = select_capability(checked, capability_id)
        if selected["operator"] != "shape_generation@1":
            raise ContractError(
                f"model service capability {selected['operator']!r} has no executable adapter"
            )
        if selected["transport"] != "remote_jobs@1":
            raise ContractError(
                f"model service transport {selected['transport']!r} has no executable adapter"
            )
        identity = RemoteIdentity(checked["service_id"], checked["backend_digest"])
        super().__init__(endpoint, identity)
        fixed = {key: thaw(self.spec.defaults[key]) for key in RESERVED_PARAMETERS}
        schema = selected["parameter_schema"]
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
            defaults={**selected["defaults"], **fixed},
        )
        self.capability_id = selected["capability_id"]
        self._legacy_wire = "capabilities" not in checked
        self.capability_digest = capability_digest(checked, self.capability_id)

    def prepare_payload(self, context: NodeExecutionContext) -> dict[str, Any]:
        parameters = self.spec.normalize_parameters(context.parameters)
        payload = {
            "operation": "shape_generation@1",
            "parameters": {
                key: thaw(value)
                for key, value in parameters.items()
                if key not in RESERVED_PARAMETERS
            },
        }
        # Keep the original flat descriptor wire contract byte-for-byte.  The
        # capability field is meaningful only for the newer multi-capability
        # service protocol.
        if not self._legacy_wire:
            payload["capability_id"] = self.capability_id
        return payload


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

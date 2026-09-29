"""Trusted executors for discovered capabilities; services cannot define port semantics."""

from dataclasses import replace
from typing import Any

from .compiled_plan import thaw
from .contracts import ContractError
from .dag_remote_adapter import RemoteNodeAdapter
from .dag_text_segmentation import RemoteTextInputSegmentationAdapter
from .model_service_descriptor import (
    RESERVED_PARAMETERS,
    DiscoveredShapeAdapter,
    capability_digest,
    select_capability,
    validate_descriptor,
)
from .remote_protocol import RemoteIdentity


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
    if selected["operator"] == "shape_generation@1":
        return DiscoveredShapeAdapter(endpoint, checked, capability_id)
    if selected["operator"] == "text_segmentation@2":
        return DiscoveredTextSegmentationAdapter(endpoint, checked, capability_id)
    raise ContractError(
        f"model service capability {selected['operator']!r} has no executable adapter"
    )

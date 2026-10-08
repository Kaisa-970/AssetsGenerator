"""Discovery for the existing evidence-checked SAM3D wire protocol."""

from dataclasses import replace
from typing import Any

from .compiled_plan import thaw
from .contracts import ContractError
from .dag_remote_masked_shape import RemoteMaskedShapeAdapter
from .model_service_descriptor import select_capability, validate_descriptor
from .remote_protocol import RemoteIdentity


def sam3d_descriptor(identity: RemoteIdentity, upstream_digest: str | None) -> dict[str, Any]:
    if upstream_digest is None:
        raise ContractError("SAM3D capability requires upstream_digest")
    spec = RemoteMaskedShapeAdapter("http://127.0.0.1:8772", identity, upstream_digest).spec
    hidden = {"remote_endpoint", "service_id", "backend_digest"}
    return {
        "schema_version": "model_service@1",
        "display_name": "SAM3D Objects",
        "service_id": identity.service_id,
        "backend_digest": identity.backend_digest,
        "capabilities": [
            {
                "capability_id": "masked_shape_generation",
                "operator": "masked_shape_generation@1",
                "transport": "remote_jobs@1",
                "frame_id": "sam3d_glb",
                "up_axis": "+Y",
                "unit": "relative_unit",
                "parameter_schema": {
                    "type": "object",
                    "properties": {
                        key: thaw(value)
                        for key, value in spec.parameter_schema["properties"].items()
                        if key not in hidden
                    },
                },
                "defaults": {
                    key: value for key, value in spec.defaults.items() if key not in hidden
                },
            }
        ],
    }


def discovered_sam3d_adapter(
    endpoint: str, descriptor: object, capability_id: str | None
) -> RemoteMaskedShapeAdapter:
    checked = validate_descriptor(descriptor)
    cap = select_capability(checked, capability_id)
    if (cap["operator"], cap["transport"]) != ("masked_shape_generation@1", "remote_jobs@1"):
        raise ContractError("SAM3D requires remote_jobs@1")
    upstream = cap["defaults"].get("upstream_digest")
    identity = RemoteIdentity(checked["service_id"], checked["backend_digest"])
    expected = sam3d_descriptor(identity, upstream)["capabilities"][0]
    for key in ("parameter_schema", "frame_id", "up_axis", "unit"):
        if cap.get(key) != expected[key]:
            raise ContractError(f"SAM3D discovery contract mismatch: {key}")
    adapter = RemoteMaskedShapeAdapter(endpoint, identity, upstream)
    adapter._spec = replace(
        adapter.spec, defaults=adapter.spec.normalize_parameters(cap["defaults"])
    )
    return adapter

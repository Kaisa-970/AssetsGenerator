from copy import deepcopy

import pytest

from assets_generator.contracts import ContractError
from assets_generator.model_service_adapters import discovered_service_adapter
from assets_generator.model_service_descriptor import validate_descriptor
from assets_generator.remote_protocol import RemoteIdentity
from assets_generator.sam3d_discovery import sam3d_descriptor


def test_sam3d_discovery_preserves_adapter_contract():
    identity = RemoteIdentity("sam3d", "sha256:" + "1" * 64)
    descriptor = validate_descriptor(sam3d_descriptor(identity, "sha256:" + "2" * 64))
    adapter = discovered_service_adapter(
        "http://127.0.0.1:8772", descriptor, "masked_shape_generation"
    )
    assert adapter.spec.operators == ("masked_shape_generation@1",)
    assert adapter.spec.defaults["upstream_digest"] == "sha256:" + "2" * 64
    for field, value in [("up_axis", "+Z"), ("parameter_schema", {"type": "object"})]:
        bad = deepcopy(descriptor)
        bad["capabilities"][0][field] = value
        with pytest.raises(ContractError):
            discovered_service_adapter("http://127.0.0.1:8772", bad, "masked_shape_generation")

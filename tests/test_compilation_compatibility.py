"""New compiler contracts must not invalidate persisted legacy workbench plans."""

from types import MappingProxyType

from assets_generator.backend_registry import resolve_plan_contract_digest
from assets_generator.contracts import OperatorSpec, PortSpec
from assets_generator.pipeline import (
    load_default_operator_specs,
    load_default_pipeline,
    load_multi_view_pipeline,
)
from assets_generator.serialization import cache_key, canonical_json_bytes, to_primitive


def test_legacy_operator_serialization_has_no_empty_relation_field():
    spec = OperatorSpec("example", "1", {"image": PortSpec(("rgb_image",))}, {})
    assert to_primitive(spec) == {
        "name": "example",
        "version": "1",
        "inputs": {"image": to_primitive(spec.inputs["image"])},
        "outputs": {},
    }


def test_existing_packaged_plan_identities_remain_readable():
    specs = load_default_operator_specs()
    assert cache_key(specs) == (
        "sha256:c993705a16829ee2cfe25a83582fb5a2d8e43d61f66cd09e07d90da5b2a9dccf"
    )
    assert resolve_plan_contract_digest(load_default_pipeline(), specs) == (
        "sha256:2cf04f2a150335ef2ee31d6f7465ea7753aa5cc107f7ba5c5183a6a3d0dad4c2"
    )
    assert resolve_plan_contract_digest(load_multi_view_pipeline(), specs) == (
        "sha256:d3c2df35efcdb61da893775f26207585d25997118bb437b74c428f5ca2023643"
    )


def test_frozen_mapping_serializes_like_existing_dicts():
    frozen = MappingProxyType({"node": MappingProxyType({"parameters": (1, 2)})})
    ordinary = {"node": {"parameters": [1, 2]}}
    assert canonical_json_bytes(frozen) == canonical_json_bytes(ordinary)

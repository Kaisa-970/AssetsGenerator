"""New compiler contracts must not invalidate persisted legacy workbench plans."""

from dataclasses import replace
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


def test_legacy_contract_serialization_preserves_historical_identities():
    # Reconstruct the pre-modular-release contract, rather than claiming that
    # new operators and a new canonicalization relation leave its identity unchanged.
    specs = load_default_operator_specs()
    for key in (
        "geometry_validation@1",
        "shape_asset_assembly@1",
        "asset_export@1",
        "selection_prepare@1",
        "image_transform@1",
        "encode_png@1",
        "resize_image@1",
        "apply_binary_mask@1",
        "text_segmentation@2",
        "text_segmentation@1",
        "select_text_mask@1",
        "masked_shape_generation@1",
        "masked_shape_asset_assembly@1",
    ):
        del specs[key]
    specs["canonicalize@1"] = replace(specs["canonicalize@1"], relations=())
    assert cache_key(specs) == (
        "sha256:f9f49f84a69fe7285a49f7be9c8bac8ed16e632e4edc142a469db54c3c3e7e6a"
    )
    assert resolve_plan_contract_digest(load_default_pipeline(), specs) == (
        "sha256:e5dd2ec56e1c3a4e4bf64e966c26d7ce8d2479e6e909e17a496e173b3fff50a7"
    )
    assert resolve_plan_contract_digest(load_multi_view_pipeline(), specs) == (
        "sha256:9c309800202157a5f7a0857e47348d7803bc27c271ecf1737544ee2469361969"
    )


def test_frozen_mapping_serializes_like_existing_dicts():
    frozen = MappingProxyType({"node": MappingProxyType({"parameters": (1, 2)})})
    ordinary = {"node": {"parameters": [1, 2]}}
    assert canonical_json_bytes(frozen) == canonical_json_bytes(ordinary)


def test_canonicalization_relation_changes_plan_identity_but_unrelated_operators_do_not():
    specs = load_default_operator_specs()
    for pipeline in (load_default_pipeline(), load_multi_view_pipeline()):
        current = resolve_plan_contract_digest(pipeline, specs)
        without_new_operators = {
            key: value
            for key, value in specs.items()
            if key
            not in {
                "geometry_validation@1",
                "shape_asset_assembly@1",
                "asset_export@1",
                "image_transform@1",
                "encode_png@1",
                "resize_image@1",
            }
        }
        assert resolve_plan_contract_digest(pipeline, without_new_operators) == current
        previous = {**specs, "canonicalize@1": replace(specs["canonicalize@1"], relations=())}
        assert resolve_plan_contract_digest(pipeline, previous) != current

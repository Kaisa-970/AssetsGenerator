from dataclasses import replace
from pathlib import Path

import pytest

from assets_generator.compiled_plan import CompiledPlan
from assets_generator.contracts import ContractError, PortSpec
from assets_generator.pipeline import compile_pipeline, load_default_operator_specs, load_pipeline


def test_comfy_chain_uses_distinct_instances_and_typed_evidence():
    pipeline = load_pipeline(Path("pipelines/comfy_image_chain_v1.yaml"))
    plan = compile_pipeline(pipeline, load_default_operator_specs(), require_explicit_joins=True)
    assert plan.topological_order == ("encode", "first", "second")
    assert plan.dependencies["second"] == ("first",)
    assert plan.nodes[1].operator == plan.nodes[2].operator
    assert plan.nodes[1].backend != plan.nodes[2].backend
    assert CompiledPlan.from_json(plan.to_json()) == plan
    spec = load_default_operator_specs()["image_transform@1"]
    assert spec.inputs["image"].kinds == ("rgb_image",)
    assert spec.outputs["evidence"].schema_name == "ComfyImageBoundary"


def test_comfy_chain_rejects_implicit_rgba_conversion():
    pipeline = load_pipeline(Path("pipelines/comfy_image_chain_v1.yaml"))
    pipeline = replace(
        pipeline, inputs={"image": PortSpec(("rgba_image",), carriers=("artifact_ref",))}
    )
    with pytest.raises(ContractError):
        compile_pipeline(pipeline, load_default_operator_specs())


def test_shipped_copy_example_binds_without_network():
    import json

    from assets_generator.comfy_profile import ComfyImageProfile
    from assets_generator.dag_adapters import AdapterRegistry
    from assets_generator.dag_comfy_profiles import register_comfy_profiles

    config = Path("examples/comfy-copy-editor.json")
    registry = AdapterRegistry()
    register_comfy_profiles(registry, json.loads(config.read_text()), base=config.parent)
    bound = registry.bind_plan(
        compile_pipeline(
            load_pipeline(Path("pipelines/comfy_image_chain_v1.yaml")),
            load_default_operator_specs(),
            require_explicit_joins=True,
        )
    )
    profile = ComfyImageProfile.load(config.parent / "comfy-copy-profile.json")
    assert bound.bindings["first"].parameters["backend_digest"] == profile.identity.backend_digest
    assert bound.bindings["second"].parameters["backend_digest"] == profile.identity.backend_digest
    assert bound.bindings["encode"].adapter == "encode_png@1"
    assert {node["class_type"] for node in profile.to_dict()["prompt"].values()} == {
        "LoadImage",
        "SaveImage",
    }

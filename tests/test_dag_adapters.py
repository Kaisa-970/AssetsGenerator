from dataclasses import replace

import pytest

from assets_generator.contracts import ContractError, OperatorSpec, PortSpec
from assets_generator.dag_adapters import (
    AdapterRegistry,
    AdapterSpec,
    BoundDagPlan,
    NodeExecutionResult,
)
from assets_generator.pipeline import PipelineDefinition, compile_pipeline


class CopyAdapter:
    spec = AdapterSpec(
        "copy",
        "1",
        ("copy@1",),
        parameter_schema={
            "type": "object",
            "properties": {
                "count": {"type": "integer", "minimum": 1, "maximum": 4},
                "options": {
                    "type": "object",
                    "properties": {"labels": {"type": "array", "items": {"type": "string"}}},
                },
            },
            "required": ["count"],
        },
        defaults={"count": 2, "options": {"labels": ["a"]}},
    )

    def execute(self, context):
        return NodeExecutionResult({"image": context.inputs["image"]})


def fixture(*, parameters=None, backend=None, adapter=None, strict=True):
    port = PortSpec(("rgb_image",))
    node = {
        "operator": "copy@1",
        "inputs": {"image": "pipeline.inputs.source"},
        "parameters": parameters or {},
    }
    if backend is not None:
        node["backend"] = backend
    if adapter is not None:
        node["adapter"] = adapter
    pipeline = PipelineDefinition("copy", "1", {"source": port}, {"instance": node})
    plan = compile_pipeline(
        pipeline,
        {"copy@1": OperatorSpec("copy", "1", {"image": port}, {"image": port})},
        require_explicit_joins=strict,
    )
    registry = AdapterRegistry()
    registry.register(CopyAdapter())
    return plan, registry


def test_binding_defaults_roundtrip_and_deep_freeze():
    plan, registry = fixture()
    bound = registry.bind_plan(plan)
    binding = bound.bindings["instance"]
    assert binding.parameters["count"] == 2
    assert binding.adapter == "copy@1"
    assert registry.resolve(binding).spec.key == "copy@1"
    assert BoundDagPlan.from_json(bound.to_json(), registry=registry) == bound
    with pytest.raises(TypeError):
        binding.parameters["options"]["labels"][0] = "changed"
    raw = bound.to_dict()
    raw["bindings"]["instance"]["parameters"]["count"] = 3
    with pytest.raises(ContractError, match="bindings"):
        BoundDagPlan.from_dict(raw, registry=registry)


def test_identity_includes_parameters_spec_and_actual_implementation():
    plan, registry = fixture()
    baseline = registry.bind_plan(plan)
    other_plan, _ = fixture(parameters={"count": 3})
    assert registry.bind_plan(other_plan).plan_id != baseline.plan_id
    registry._adapters["copy@1"].spec = replace(CopyAdapter.spec, defaults={"count": 3})
    assert registry.bind_plan(plan).plan_id != baseline.plan_id
    with pytest.raises(ContractError, match="identity changed"):
        registry.resolve(baseline.bindings["instance"])


@pytest.mark.parametrize(
    "parameters",
    [
        {"count": True},
        {"count": 0},
        {"count": 5},
        {"extra": 1},
        {"options": {"extra": 1}},
        {"options": {"labels": [1]}},
    ],
)
def test_bad_parameters_rejected(parameters):
    plan, registry = fixture(parameters=parameters)
    with pytest.raises(ContractError):
        registry.bind_plan(plan)


def test_legacy_and_backend_requests_are_not_executable():
    for options in ({"strict": False}, {"backend": "trellis2"}, {"adapter": "missing@1"}):
        plan, registry = fixture(**options)
        with pytest.raises(ContractError):
            registry.bind_plan(plan)


def test_ambiguous_or_duplicate_adapter_rejected():
    plan, registry = fixture()
    with pytest.raises(ContractError, match="duplicate"):
        registry.register(CopyAdapter())
    alternate = CopyAdapter()
    alternate.spec = replace(alternate.spec, name="alternate")
    registry.register(alternate)
    with pytest.raises(ContractError, match="exactly one"):
        registry.bind_plan(plan)
    plan, _ = fixture(adapter="copy@1")
    assert registry.bind_plan(plan).bindings["instance"].adapter == "copy@1"


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "patternProperties": {}},
        {"type": "object", "required": ["unknown"]},
        {"type": "object", "additionalProperties": {}},
        {"type": "object", "properties": {"x": {"type": "integer", "minimum": 4, "maximum": 1}}},
        {"type": "object", "properties": {"x": {"type": "array"}}},
        {"type": "object", "properties": {"x": {"type": "string", "minimum": 2}}},
    ],
)
def test_schema_subset_fails_closed(schema):
    with pytest.raises(ContractError):
        AdapterSpec("bad", "1", ("copy@1",), parameter_schema=schema)


def test_non_cpu_execution_and_unvalidated_human_requests_rejected():
    with pytest.raises(ContractError):
        AdapterSpec("gpu", "1", ("copy@1",), execution_kind="native_process")
    with pytest.raises(ContractError):
        NodeExecutionResult(wait_request={"request": "bad"})


def test_bound_json_rejects_duplicate_keys():
    plan, registry = fixture()
    bound = registry.bind_plan(plan)
    with pytest.raises(ContractError):
        BoundDagPlan.from_json('{"plan_id":"x",' + bound.to_json()[1:], registry=registry)


def test_bound_record_rejects_numeric_boolean_alias():
    plan, registry = fixture(parameters={"count": 1})
    bound = registry.bind_plan(plan)
    raw = bound.to_dict()
    raw["bindings"]["instance"]["parameters"]["count"] = True
    with pytest.raises(ContractError, match="bindings"):
        BoundDagPlan.from_dict(raw, registry=registry)


class DifferentCopyAdapter(CopyAdapter):
    pass


def test_actual_class_identity_changes_plan_and_refuses_stale_load():
    plan, registry = fixture()
    before = registry.bind_plan(plan)
    updated = AdapterRegistry()
    updated.register(DifferentCopyAdapter())
    after = updated.bind_plan(plan)
    assert before.plan_id != after.plan_id
    with pytest.raises(ContractError, match="bindings"):
        BoundDagPlan.from_dict(before.to_dict(), registry=updated)


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_parameter_identity_rejected(value):
    with pytest.raises(ContractError):
        fixture(parameters={"count": value})


def test_explicit_backends_are_pinned_and_do_not_change_legacy_binding():
    plan, registry = fixture()
    legacy = registry.bind_plan(plan)
    first = CopyAdapter()
    second = CopyAdapter()
    second.spec = replace(CopyAdapter.spec, defaults={"count": 3})
    registry.register_backend("local-a", first)
    registry.register_backend("local-b", second)
    assert registry.bind_plan(plan).to_dict() == legacy.to_dict()
    assert "backend" not in legacy.bindings["instance"].to_dict()
    a, _ = fixture(backend="local-a")
    b, _ = fixture(backend="local-b")
    bound_a, bound_b = registry.bind_plan(a), registry.bind_plan(b)
    assert bound_a.plan_id != bound_b.plan_id
    assert registry.resolve(bound_a.bindings["instance"]) is first
    assert registry.resolve(bound_b.bindings["instance"]) is second
    assert bound_b.bindings["instance"].parameters["count"] == 3
    assert BoundDagPlan.from_json(bound_b.to_json(), registry=registry) == bound_b
    assert len(registry.backend_catalog()) == 2
    second.spec = replace(second.spec, defaults={"count": 4})
    with pytest.raises(ContractError, match="identity changed"):
        registry.resolve(bound_b.bindings["instance"])
    with pytest.raises(ContractError, match="bindings"):
        BoundDagPlan.from_json(bound_b.to_json(), registry=registry)
    assert registry.resolve(bound_a.bindings["instance"]) is first


def test_backend_binding_rejects_missing_or_wrong_operator():
    plan, registry = fixture(backend="absent")
    with pytest.raises(ContractError, match="unresolved Backend"):
        registry.bind_plan(plan)
    wrong = CopyAdapter()
    wrong.spec = replace(CopyAdapter.spec, operators=("other@1",))
    registry.register_backend("absent", wrong)
    with pytest.raises(ContractError, match="compatible adapter"):
        registry.bind_plan(plan)
    with pytest.raises(ContractError, match="duplicate"):
        registry.register_backend("absent", wrong)


def test_two_instances_resolve_different_backends_in_one_plan():
    port = PortSpec(("rgb_image",))
    definition = PipelineDefinition(
        "pair",
        "1",
        {"image": port},
        {
            name: {
                "operator": "copy@1",
                "adapter": "copy@1",
                "backend": backend,
                "inputs": {"image": "pipeline.inputs.image"},
            }
            for name, backend in (("left", "a"), ("right", "b"))
        },
    )
    registry = AdapterRegistry()
    a, b = CopyAdapter(), CopyAdapter()
    b.spec = replace(CopyAdapter.spec, defaults={"count": 4})
    registry.register_backend("a", a)
    registry.register_backend("b", b)
    plan = registry.bind_plan(
        compile_pipeline(
            definition,
            {"copy@1": OperatorSpec("copy", "1", {"image": port}, {"image": port})},
            require_explicit_joins=True,
        )
    )
    assert plan.bindings["left"].backend == "a"
    assert plan.bindings["right"].backend == "b"
    assert plan.bindings["left"].parameters["count"] == 2
    assert plan.bindings["right"].parameters["count"] == 4
    assert BoundDagPlan.from_json(plan.to_json(), registry=registry) == plan

from dataclasses import replace

import pytest

from assets_generator.compiled_plan import CompiledPlan, digest
from assets_generator.contracts import ContractError, OperatorSpec, PortSpec, RelationSpec
from assets_generator.pipeline import (
    PipelineDefinition,
    compile_pipeline,
    load_default_operator_specs,
    load_default_pipeline,
    load_multi_view_pipeline,
)


def diamond():
    port = PortSpec(("rgb_image",))
    single = OperatorSpec("copy", "1", {"image": port}, {"image": port})
    join = OperatorSpec(
        "join",
        "1",
        {"left": port, "right": port},
        {"image": port},
        (RelationSpec("independent_inputs@1", ("left", "right")),),
    )
    pipeline = PipelineDefinition(
        "diamond",
        "1",
        {"source": port},
        {
            "D": {
                "operator": "join@1",
                "inputs": {"left": "B.outputs.image", "right": "C.outputs.image"},
            },
            "C": {"operator": "copy@1", "inputs": {"image": "A.outputs.image"}},
            "B": {"operator": "copy@1", "inputs": {"image": "A.outputs.image"}},
            "A": {
                "operator": "copy@1",
                "inputs": {"image": "pipeline.inputs.source"},
                "parameters": {"nested": {"list": [1, 2]}},
                "backend": "example",
            },
        },
    )
    return pipeline, {"copy@1": single, "join@1": join}


def test_diamond_stable_dependencies_and_roundtrip():
    pipeline, specs = diamond()
    plan = compile_pipeline(pipeline, specs, require_explicit_joins=True)
    assert plan.topological_order == ("A", "B", "C", "D")
    assert plan.dependencies["D"] == ("B", "C")
    assert plan.dependents["A"] == ("B", "C")
    assert plan.nodes[1].inputs["image"] == plan.nodes[2].inputs["image"]
    assert plan.nodes[3].relations[0]["name"] == "independent_inputs"
    assert plan.resolution_status == "unresolved"
    assert plan.nodes[0].backend == "example"
    assert CompiledPlan.from_json(plan.to_json()) == plan
    reversed_pipeline = replace(pipeline, nodes=dict(reversed(list(pipeline.nodes.items()))))
    assert compile_pipeline(reversed_pipeline, specs, require_explicit_joins=True) == plan


def test_nested_freeze_and_detached_serialization():
    pipeline, specs = diamond()
    plan = compile_pipeline(pipeline, specs)
    original = plan.to_json()
    pipeline.nodes["A"]["parameters"]["nested"]["list"].append(3)
    with pytest.raises(TypeError):
        plan.nodes[0].parameters["nested"]["list"][0] = 4
    with pytest.raises(TypeError):
        plan.inputs["source"].contract["requires_frame"] = True
    with pytest.raises(TypeError):
        plan.dependencies["A"] = ("D",)
    raw = plan.to_dict()
    raw["nodes"][0]["parameters"]["nested"]["list"].append(4)
    assert plan.to_json() == original


@pytest.mark.parametrize("change", ["parameter", "backend", "adapter", "edge", "input", "operator"])
def test_execution_changes_identity(change):
    pipeline, specs = diamond()
    before = compile_pipeline(pipeline, specs).plan_id
    if change == "parameter":
        pipeline.nodes["A"]["parameters"]["seed"] = 5
    elif change == "backend":
        pipeline.nodes["A"]["backend"] = "other"
    elif change == "adapter":
        pipeline.nodes["A"]["adapter"] = "copy@1"
    elif change == "edge":
        pipeline.nodes["C"]["inputs"]["image"] = "pipeline.inputs.source"
    elif change == "input":
        pipeline.inputs["source"] = replace(pipeline.inputs["source"], schema_version="2")
    else:
        specs["copy@1"].outputs["image"] = replace(specs["copy@1"].outputs["image"], persist=True)
    assert compile_pipeline(pipeline, specs).plan_id != before


def test_ui_metadata_is_not_execution_identity():
    pipeline, specs = diamond()
    before = compile_pipeline(pipeline, specs)
    pipeline.nodes["A"].update(label="Pretty", ui_layout={"x": 99, "y": 32})
    assert compile_pipeline(pipeline, specs) == before


@pytest.mark.parametrize(
    "field",
    [
        "dependencies",
        "dependents",
        "topological_order",
        "parameters",
        "unknown",
        "resolution_status",
    ],
)
def test_decode_rejects_forged_derived_fields_even_with_new_digest(field):
    pipeline, specs = diamond()
    raw = compile_pipeline(pipeline, specs).to_dict()
    if field in ("dependencies", "dependents"):
        raw[field]["A"] = ["D"]
    elif field == "topological_order":
        raw[field].reverse()
    elif field == "parameters":
        raw["nodes"][0]["parameters"]["nested"]["list"].append(7)
        # A valid changed declaration with a recomputed digest is a new valid plan;
        # stale identity must still reject edits.
        with pytest.raises(ContractError):
            CompiledPlan.from_dict(raw)
        return
    else:
        raw[field] = "unexpected"
    raw["plan_id"] = digest({key: value for key, value in raw.items() if key != "plan_id"})
    with pytest.raises(ContractError):
        CompiledPlan.from_dict(raw)


def test_invalid_json_duplicate_nodes_and_unsupported_schema():
    pipeline, specs = diamond()
    plan = compile_pipeline(pipeline, specs)
    raw = plan.to_dict()
    raw["nodes"].append(raw["nodes"][0])
    with pytest.raises(ContractError, match="duplicate"):
        CompiledPlan.from_dict(raw)
    with pytest.raises(ContractError, match="duplicate"):
        CompiledPlan.from_json('{"a":1,"a":2}')
    raw = plan.to_dict()
    raw["schema_version"] = "future"
    with pytest.raises(ContractError, match="schema"):
        CompiledPlan.from_dict(raw)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object(), {1: "bad"}])
def test_non_json_parameters_rejected(value):
    pipeline, specs = diamond()
    pipeline.nodes["A"]["parameters"]["bad"] = value
    with pytest.raises(ContractError):
        compile_pipeline(pipeline, specs)


@pytest.mark.parametrize("source", [load_default_pipeline, load_multi_view_pipeline])
def test_existing_pipeline_roundtrip_and_strict_join_migration_boundary(source):
    pipeline = source()
    specs = load_default_operator_specs()
    plan = compile_pipeline(pipeline, specs)
    assert CompiledPlan.from_json(plan.to_json()) == plan
    with pytest.raises(ContractError, match="relation"):
        compile_pipeline(pipeline, specs, require_explicit_joins=True)


def test_bad_references_cycles_unknown_fields_and_registry_identity():
    pipeline, specs = diamond()
    pipeline.nodes["A"]["inputs"]["image"] = "D.outputs.image"
    with pytest.raises(ContractError, match="cycle"):
        compile_pipeline(pipeline, specs)
    pipeline.nodes["A"]["inputs"]["image"] = "pipeline.inputs.source??"
    with pytest.raises(ContractError, match="reference"):
        compile_pipeline(pipeline, specs)
    pipeline.nodes["A"]["inputs"]["image"] = "pipeline.inputs.source"
    pipeline.nodes["A"]["typo"] = True
    with pytest.raises(ContractError, match="unknown fields"):
        compile_pipeline(pipeline, specs)
    del pipeline.nodes["A"]["typo"]
    specs["copy@1"] = replace(specs["copy@1"], name="not_copy")
    with pytest.raises(ContractError, match="identity"):
        compile_pipeline(pipeline, specs)


@pytest.mark.parametrize("required", ["requires_frame", "requires_unit"])
def test_spatial_source_must_guarantee_required_metadata(required):
    pipeline, specs = diamond()
    specs["copy@1"].inputs["image"] = replace(specs["copy@1"].inputs["image"], **{required: True})
    with pytest.raises(ContractError, match="guarantee"):
        compile_pipeline(pipeline, specs)


@pytest.mark.parametrize("field", ["requires_frame", "requires_unit", "persist"])
@pytest.mark.parametrize("location", ["pipeline", "operator"])
def test_compiled_port_rejects_non_boolean_flags(field, location):
    pipeline, specs = diamond()
    if location == "pipeline":
        pipeline.inputs["source"] = replace(pipeline.inputs["source"], **{field: "false"})
    else:
        specs["copy@1"].outputs["image"] = replace(
            specs["copy@1"].outputs["image"], **{field: "false"}
        )
    with pytest.raises(ContractError, match="boolean"):
        compile_pipeline(pipeline, specs)

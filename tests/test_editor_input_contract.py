import pytest

from assets_generator.contracts import ContractError
from assets_generator.node_editor_execution import validate_editor_inputs
from assets_generator.pipeline import _pipeline_from_raw, compile_pipeline


def plan(kind, schema=None, version=None):
    name = "observations" if kind == "observation_bundle" else "image"
    contract = {"kind": kind, "carriers": ["artifact_ref"]}
    if schema is not None:
        contract["schema_name"] = schema
    if version is not None:
        contract["schema_version"] = version
    return compile_pipeline(
        _pipeline_from_raw(
            {"pipeline": "entry", "version": "1", "inputs": {name: contract}, "nodes": {}}
        ),
        {},
        require_explicit_joins=True,
    )


@pytest.mark.parametrize(
    "kind,schema", [("rgba_image", "png"), ("observation_bundle", "ObservationBundle")]
)
def test_fixed_input_schema_gate(kind, schema):
    assert validate_editor_inputs(plan(kind, schema, "1.0"))
    assert validate_editor_inputs(plan(kind))
    with pytest.raises(ContractError, match="schema_name"):
        validate_editor_inputs(plan(kind, "WrongSchema", "1.0"))
    with pytest.raises(ContractError, match="schema_version"):
        validate_editor_inputs(plan(kind, schema, "2.0"))


def test_rgb_references_remain_validated_against_actual_artifact_at_start():
    assert validate_editor_inputs(plan("rgb_image", "png", "1.0")) == "image"
    assert validate_editor_inputs(plan("rgb_image", "raster_image", "1.0")) == "image"


def test_editor_compile_reports_ineligible_before_reading_input(tmp_path):
    from test_dag_image_adapters import image_plan
    from test_workbench_engine import fixture_engine

    from assets_generator.dag_engine import DagEngine
    from assets_generator.dag_persistence import DagRepository
    from assets_generator.node_editor import DraftEditor
    from assets_generator.node_editor_execution import NodeEditorExecution

    store, _, profile = fixture_engine(tmp_path)
    registry, _ = image_plan(profile)
    graph = {
        "pipeline": "entry",
        "version": "1",
        "nodes": {},
        "inputs": {
            "image": {
                "kind": "rgba_image",
                "carriers": ["artifact_ref"],
                "schema_name": "raster_image",
                "schema_version": "1.0",
            }
        },
    }
    with DagRepository(store, tmp_path / "dag") as repo:
        service = NodeEditorExecution(DagEngine(repo, registry))
        try:
            result = DraftEditor(tmp_path / "drafts", execution=service).compile(graph)
            assert result["ok"] and result["bound_plan"]
            assert result["execution_ready"] is False
            assert "schema_name=png" in result["execution_reason"]
            with pytest.raises(ContractError) as caught:
                service.start(graph, "/must-not-read.png")
            assert str(caught.value) == result["execution_reason"]
            assert service.list_runs() == []
        finally:
            service.close()

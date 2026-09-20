import pytest
from PIL import Image
from test_node_editor_execution import wait

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError, OperatorSpec, PortSpec, RelationSpec
from assets_generator.dag_adapters import AdapterRegistry, AdapterSpec, NodeExecutionResult
from assets_generator.dag_engine import DagEngine
from assets_generator.dag_persistence import DagRepository
from assets_generator.node_editor import DraftEditor
from assets_generator.node_editor_execution import NodeEditorExecution
from assets_generator.relations import RelationValidatorRegistry, RelationValidatorSpec
from assets_generator.serialization import sha256_bytes


class SameInput:
    spec = RelationValidatorSpec("same_input_fixture", "1", sha256_bytes(b"same-input-v1"))

    def __init__(self):
        self.checks = 0

    def validate_static(self, context):
        assert set(context.inputs) == {"left", "right"}

    def validate_runtime(self, context):
        self.checks += 1
        assert context.values["left"] == context.values["right"]


class RejectInput(SameInput):
    spec = RelationValidatorSpec("same_input_fixture", "1", sha256_bytes(b"reject-input-v1"))

    def validate_runtime(self, context):
        self.checks += 1
        raise ContractError("fixture observation relation rejected")


class Join:
    spec = AdapterSpec("fixture_join", "1", ("fixture_join@1",))

    def __init__(self):
        self.calls = 0

    def execute(self, context):
        self.calls += 1
        return NodeExecutionResult({"image": context.inputs["left"]})


@pytest.mark.parametrize("reject", [False, True])
def test_custom_relation_compiles_runs_and_recovers_through_editor(tmp_path, reject):
    port = PortSpec(("rgb_image",), carriers=("artifact_ref",))
    specs = {
        "fixture_join@1": OperatorSpec(
            "fixture_join",
            "1",
            {"left": port, "right": port},
            {"image": port},
            (RelationSpec("same_input_fixture@1", ("left", "right")),),
        )
    }
    relation = RejectInput() if reject else SameInput()
    relations = RelationValidatorRegistry()
    relations.register(relation)
    adapter = Join()
    registry = AdapterRegistry()
    registry.register(adapter)
    graph = {
        "pipeline": "custom_relation",
        "version": "1",
        "inputs": {"image": {"kind": "rgb_image", "carriers": ["artifact_ref"]}},
        "nodes": {
            "join": {
                "operator": "fixture_join@1",
                "inputs": {"left": "pipeline.inputs.image", "right": "pipeline.inputs.image"},
            }
        },
    }
    image = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(image)
    store = LocalArtifactStore(tmp_path / "store")
    directory = tmp_path / "runtime"
    with DagRepository(store, directory) as repo:
        service = NodeEditorExecution(DagEngine(repo, registry, relations), specs=specs)
        try:
            compiled = DraftEditor(tmp_path / "drafts", execution=service).compile(graph)
            assert compiled["ok"] and compiled["execution_ready"]
            started = service.start(graph, str(image))
            run_id = started["run"]["run_id"]
            completed = wait(service, run_id)
            assert completed["run"]["status"] == ("failed" if reject else "succeeded"), completed
            assert adapter.calls == (0 if reject else 1)
            if reject:
                attempt = completed["run"]["dag"]["node_states"]["join"]["attempts"][-1]
                assert "fixture observation relation rejected" in attempt["error_detail"]
                assert attempt["error_code"]
                assert attempt["outputs"] == {}
            assert relation.checks > 0
            plan = service.plan(run_id)
            assert plan["plan_id"] == compiled["bound_plan"]["plan_id"]
        finally:
            service.close()
    with DagRepository(store, directory) as repo:
        engine = DagEngine(repo, registry, relations)
        recovered = engine.drain(run_id)
        assert recovered.status == ("failed" if reject else "succeeded")
        assert adapter.calls == (0 if reject else 1)
        if reject:
            state = recovered.dag.node_states["join"]
            assert "fixture observation relation rejected" in state.current().error_detail
            assert state.current().status == "failed"
        assert len(recovered.dag.node_states["join"].attempts) == 1


def test_editor_rejects_separate_relation_registry_before_creating_work(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    with DagRepository(store, tmp_path / "runtime") as repo:
        engine_relations = RelationValidatorRegistry()
        separate_relations = RelationValidatorRegistry()
        # Matching current contents do not prevent future independent mutation.
        engine_relations.register(SameInput())
        separate_relations.register(SameInput())
        engine = DagEngine(repo, AdapterRegistry(), engine_relations)
        with pytest.raises(ContractError, match="share the engine relation registry"):
            NodeEditorExecution(engine, relations=separate_relations)
        service = NodeEditorExecution(engine, relations=engine_relations)
        try:
            assert service.relations is engine.relations
            assert service.list_runs() == []
        finally:
            service.close()

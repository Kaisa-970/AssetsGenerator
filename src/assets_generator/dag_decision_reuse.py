"""Read-only validation of an explicitly proposed cross-run human decision."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .contracts import ContractError
from .models import ArtifactRef
from .serialization import canonical_json_bytes, to_primitive
from .workbench_models import read_build_run

if TYPE_CHECKING:
    from .dag_engine import DagEngine
    from .models import BuildRun


def decision_proposal(
    engine: DagEngine, target: BuildRun, node_id: str, source: ArtifactRef
) -> dict[str, Any]:
    """Require exact evidence and implementation identity; never execute or repair."""
    engine.repository.verify_reference_closure(source)
    identity = engine.store.get_manifest(source.artifact_id).identity
    if (identity.kind, identity.schema_name, identity.schema_version) != (
        "build_run",
        "BuildRun",
        "1.0",
    ):
        raise ContractError("decision source must be a BuildRun snapshot")
    original = read_build_run(engine.store.read_structured(source))
    if original.run_id == target.run_id or original.dag is None or target.dag is None:
        raise ContractError("decision reuse requires a different DAG run")
    if node_id not in original.dag.node_states or node_id not in target.dag.node_states:
        raise ContractError("decision source node missing")
    old_state, new_state = original.dag.node_states[node_id], target.dag.node_states[node_id]
    if old_state.status != "succeeded" or new_state.status != "waiting_for_input":
        raise ContractError("decision reuse requires successful source and waiting target")
    old, new = old_state.current(), new_state.current()
    old_plan, new_plan = engine._plan(original), engine._plan(target)
    old_node = next(n for n in old_plan.static_plan.nodes if n.node_id == node_id)
    new_node = next(n for n in new_plan.static_plan.nodes if n.node_id == node_id)
    if (
        old.decision is None
        or new.decision is not None
        or new_plan.bindings[node_id].spec["execution_kind"] != "human"
        or canonical_json_bytes(old_plan.bindings[node_id].to_dict())
        != canonical_json_bytes(new_plan.bindings[node_id].to_dict())
        or old_node.operator != new_node.operator
        or old_node.operator_contract_digest != new_node.operator_contract_digest
        or canonical_json_bytes(old.resolved_inputs) != canonical_json_bytes(new.resolved_inputs)
        or canonical_json_bytes(engine._inputs(target, new_node))
        != canonical_json_bytes(new.resolved_inputs)
    ):
        raise ContractError("human decision inputs or binding differ; select again")
    engine._human_evidence(original, old_plan, old_node, old)
    engine._human_evidence(target, new_plan, new_node, new)
    engine._validate_inputs(new_node, new.resolved_inputs)
    assert old.request is not None and new.request is not None
    old_request = engine.store.read_structured(old.request)
    new_request = engine.store.read_structured(new.request)
    # Only run-local request ownership can differ. All decision semantics and
    # referenced candidates must match, including any adapter-specific fields.
    ownership = {"run_id", "node_id", "attempt_id", "input_digest"}
    if canonical_json_bytes({k: v for k, v in old_request.items() if k not in ownership}) != (
        canonical_json_bytes({k: v for k, v in new_request.items() if k not in ownership})
    ):
        raise ContractError("human decision request changed; select again")
    record = engine.store.read_structured(old.decision)
    return {
        "source_snapshot": to_primitive(source),
        "source_run_id": original.run_id,
        "node_id": node_id,
        "source_decision": to_primitive(old.decision),
        "source_reviewer": record["reviewer"],
        "payload": record["payload"],
    }

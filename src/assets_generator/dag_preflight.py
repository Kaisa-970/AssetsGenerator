"""Read-only execution explanation, pinned to exact plans, inputs and source evidence.

This checks Core contracts/relations, not arbitrary adapter-specific input semantics,
remote service availability or compute admission. No adapter methods are invoked.
"""

from __future__ import annotations

import copy
from typing import Any

from .compiled_plan import digest, thaw
from .contracts import ContractError, validate_port_value
from .dag_adapters import BoundDagPlan
from .dag_engine import DagEngine
from .dag_models import DagAttempt, DagNodeState, DagState, PortMap
from .dag_reuse import REUSABLE, verify_source
from .models import ArtifactRef, BuildRun
from .pipeline import _port_spec
from .serialization import canonical_json_bytes, to_primitive
from .workbench_models import read_build_run

_CHECK_ERRORS = (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration)


def analyze_execution(
    engine: DagEngine,
    plan: BoundDagPlan,
    inputs: PortMap,
    snapshot: ArtifactRef | None = None,
) -> dict[str, Any]:
    """Explain creation-time reuse without creating artifacts or modifying any run.

    Only verified reused outputs make downstream inputs known. Nodes behind planned
    computation remain ``await_upstream``; this does not promise dynamic cache hits.
    Source closure verification intentionally retains the existing whole-snapshot
    policy: even unrelated corrupt historical evidence blocks requested source reuse.
    The digest binds this explanation, rather than authorizing future execution.
    """
    rows: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    missing: set[str] = set()
    global_error: tuple[str, str] | None = None
    source = None
    raw_source_plan: dict[str, Any] = {}
    try:
        plan = BoundDagPlan.from_dict(
            plan.to_dict(), registry=engine.registry, relation_registry=engine.relations
        )
        if set(inputs) - set(plan.static_plan.inputs):
            raise ContractError("unknown pipeline inputs")
    except _CHECK_ERRORS as error:
        global_error = ("plan_invalid", str(error))
    if global_error is None:
        for name, port in plan.static_plan.inputs.items():
            spec = _port_spec(thaw(port.contract))
            if name not in inputs and spec.cardinality in {"one", "one_or_more"}:
                missing.add(name)
                continue
            try:
                validate_port_value(
                    operator=plan.static_plan.pipeline_name,
                    port_name=name,
                    spec=spec,
                    value=inputs.get(name),
                    store=engine.store,
                )
                engine._verify_values(inputs.get(name))
            except _CHECK_ERRORS as error:
                errors[name] = str(error)
    if snapshot is not None and global_error is None:
        try:
            engine.repository.verify_reference_closure(snapshot)
            identity = engine.store.get_manifest(snapshot.artifact_id).identity
            if (identity.kind, identity.schema_name, identity.schema_version) != (
                "build_run",
                "BuildRun",
                "1.0",
            ):
                raise ContractError("reuse source must be a BuildRun snapshot")
            source = read_build_run(engine.store.read_structured(snapshot))
            if source.dag is None:
                raise ContractError("reuse requires DAG source")
            raw_source_plan = engine.store.read_structured(source.dag.plan)
        except _CHECK_ERRORS as error:
            global_error = ("source_snapshot_evidence_invalid", str(error))
    # This placeholder plan reference is only used by the in-memory input resolver.
    run = BuildRun(
        "preflight",
        plan.static_plan.pipeline_name,
        plan.static_plan.pipeline_version,
        "running",
        {},
        [],
        "",
        None,
        dag=DagState(
            ArtifactRef(plan.plan_id),
            plan.plan_id,
            copy.deepcopy(inputs),
            {node.node_id: DagNodeState(node.node_id) for node in plan.static_plan.nodes},
        ),
    )
    assert run.dag is not None
    for node in plan.static_plan.nodes:

        def result(status: str, reason: str, detail: str) -> dict[str, Any]:
            return {"status": status, "reason": reason, "detail": detail}

        if global_error is not None:
            rows[node.node_id] = result("blocked", *global_error)
            continue
        parents = plan.static_plan.dependencies[node.node_id]
        names = {item.port for item in node.inputs.values() if item.source == "pipeline_input"}
        if names & errors.keys():
            rows[node.node_id] = result(
                "blocked",
                "input_invalid",
                "; ".join(errors[name] for name in sorted(names & errors.keys())),
            )
            continue
        if any(rows[parent]["status"] == "blocked" for parent in parents):
            rows[node.node_id] = result(
                "blocked", "upstream_blocked", "required upstream evidence is blocked"
            )
            continue
        if names & missing or any(rows[parent]["status"] != "reuse" for parent in parents):
            rows[node.node_id] = result(
                "await_upstream",
                "inputs_not_yet_known",
                "required input references or upstream results are not yet known",
            )
            continue
        try:
            values = engine._inputs(run, node)
            for name, contract in node.operator_contract["inputs"].items():
                validate_port_value(
                    operator=node.operator,
                    port_name=name,
                    spec=_port_spec(thaw(contract)),
                    value=values.get(name),
                    store=engine.store,
                )
            engine._validate_inputs(node, values)
            binding = plan.bindings[node.node_id]
            state = source.dag.node_states.get(node.node_id) if source and source.dag else None
            candidate = (
                state is not None
                and state.status == "succeeded"
                and binding.adapter in REUSABLE
                and canonical_json_bytes(raw_source_plan["bindings"].get(node.node_id))
                == canonical_json_bytes(binding.to_dict())
                and state.current().input_digest == engine._input_digest(plan, node, values)
            )
            if candidate:
                assert snapshot is not None
                attempt = verify_source(engine, snapshot, node, plan, values)
                run.dag.node_states[node.node_id] = DagNodeState(
                    node.node_id,
                    "succeeded",
                    [DagAttempt(1, status="succeeded", outputs=copy.deepcopy(attempt.outputs))],
                )
                rows[node.node_id] = {
                    **result("reuse", "verified_exact_match", "source identity and evidence match"),
                    "outputs": to_primitive(attempt.outputs),
                    "input_digest": attempt.input_digest,
                }
            else:
                rows[node.node_id] = result(
                    "execute",
                    "no_verified_reuse",
                    "no creation-time reuse; adapter-specific validation remains at execution",
                )
        except _CHECK_ERRORS as error:
            rows[node.node_id] = result("blocked", "node_evidence_invalid", str(error))
    report: dict[str, Any] = {
        "schema_version": "execution_preflight@1",
        "plan_id": plan.plan_id,
        "inputs": to_primitive(inputs),
        "reuse_source": to_primitive(snapshot),
        "nodes": rows,
        "input_errors": errors,
        "missing_inputs": sorted(missing),
        "execution_ready": global_error is None
        and not errors
        and not missing
        and all(row["status"] != "blocked" for row in rows.values()),
        "validation_scope": "core_contracts_relations_and_reuse_evidence",
        "source_evidence_policy": "whole_snapshot_closure",
    }
    if global_error:
        report["error"] = {"reason": global_error[0], "detail": global_error[1]}
    report["digest"] = digest(report)
    return report

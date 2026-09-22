"""Read-only, explicit reuse of verified node results from immutable run snapshots."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from .compiled_plan import digest, thaw
from .contracts import ContractError
from .dag_models import DagAttempt, PortMap
from .dag_provenance import expected_node_provenance_records, persist_node_provenance
from .models import ArtifactRef
from .remote_protocol import RemoteJob
from .runtime import utc_now
from .serialization import canonical_json_bytes
from .workbench_models import read_build_run

if TYPE_CHECKING:
    from .compiled_plan import CompiledNode
    from .dag_adapters import BoundDagPlan
    from .dag_engine import DagEngine
    from .models import BuildRun

# These adapters produce artifacts, not human decisions or publication side effects.
REUSABLE = {
    "remote_text_segmentation@1",
    "remote_text_segmentation@2",
    "remote_masked_shape@1",
    "remote_shape@1",
    "apply_binary_mask@1",
    "resize_image@1",
    "encode_png@1",
}


def verify_source(
    engine: DagEngine,
    snapshot: ArtifactRef,
    node: CompiledNode,
    plan: BoundDagPlan,
    inputs: PortMap,
    seen: set[str] | None = None,
) -> DagAttempt:
    seen = set() if seen is None else seen
    if snapshot.artifact_id in seen:
        raise ContractError("reuse evidence cycle")
    seen.add(snapshot.artifact_id)
    engine.repository.verify_reference_closure(snapshot)
    identity = engine.store.get_manifest(snapshot.artifact_id).identity
    if (identity.kind, identity.schema_name, identity.schema_version) != (
        "build_run",
        "BuildRun",
        "1.0",
    ):
        raise ContractError("reuse source must be a BuildRun snapshot")
    source = read_build_run(engine.store.read_structured(snapshot))
    if source.dag is None or node.node_id not in source.dag.node_states:
        raise ContractError("reuse source node missing")
    state = source.dag.node_states[node.node_id]
    attempt = state.current()
    binding = plan.bindings[node.node_id]
    raw_plan = engine.store.read_structured(source.dag.plan)
    source_node = next(n for n in raw_plan["static_plan"]["nodes"] if n["node_id"] == node.node_id)
    if (
        state.status != "succeeded"
        or attempt.status != "succeeded"
        or attempt.operator != node.operator
        or attempt.adapter != binding.adapter
        or attempt.adapter not in REUSABLE
        or attempt.decision is not None
        or attempt.child_registration is not None
        or canonical_json_bytes(raw_plan["bindings"][node.node_id])
        != canonical_json_bytes(binding.to_dict())
        or source_node["operator_contract_digest"] != node.operator_contract_digest
        or attempt.input_digest != engine._input_digest(plan, node, inputs)
        or canonical_json_bytes(attempt.resolved_inputs) != canonical_json_bytes(inputs)
        or attempt.binding_digest != digest(binding.to_dict())
        or attempt.parameters_digest != digest(binding.parameters)
    ):
        raise ContractError("reuse identity mismatch")
    engine._validate_inputs(node, inputs)
    engine._validate_outputs(node, attempt.outputs)
    if attempt.reused_from is not None:
        original = verify_source(engine, attempt.reused_from, node, plan, inputs, seen)
        if canonical_json_bytes(original.outputs) != canonical_json_bytes(attempt.outputs):
            raise ContractError("reused outputs differ from source")
    elif binding.spec["execution_kind"] == "remote":
        if attempt.remote_binding is None or attempt.remote_result is None:
            raise ContractError("reuse remote evidence missing")
        if (
            attempt.remote_binding.run_id != source.run_id
            or attempt.remote_binding.node_id != node.node_id
            or attempt.remote_binding.attempt != attempt.attempt
            or attempt.remote_binding.input_digest != attempt.input_digest
            or attempt.remote_binding.binding_digest != attempt.binding_digest
        ):
            raise ContractError("reuse remote ownership mismatch")
        raw = engine.store.read_structured(attempt.remote_result)
        if canonical_json_bytes(raw["binding"]) != canonical_json_bytes(attempt.remote_binding):
            raise ContractError("reuse remote result binding mismatch")
        if RemoteJob.parse(raw["job"], attempt.remote_binding.request()).state != "succeeded":
            raise ContractError("reuse remote result not successful")
    expected = expected_node_provenance_records(
        engine.store,
        run_id=source.run_id,
        node_id=node.node_id,
        attempt=attempt.attempt,
        operator=node.operator,
        adapter_identity=binding.to_dict(),
        parameters=thaw(binding.parameters),
        inputs=inputs,
        outputs=attempt.outputs,
        execution_evidence=attempt.reused_from or attempt.remote_result,
    )
    actual = {
        port: [engine.store.read_structured(ref) for ref in refs]
        for port, refs in attempt.provenance.items()
    }
    if canonical_json_bytes(expected) != canonical_json_bytes(actual):
        raise ContractError("reuse provenance mismatch")
    return attempt


def seed_reuse(engine: DagEngine, run: BuildRun, plan: BoundDagPlan, snapshot: ArtifactRef) -> None:
    """Only exact matches are adopted; damaged source evidence aborts before creation."""
    engine.repository.verify_reference_closure(snapshot)
    identity = engine.store.get_manifest(snapshot.artifact_id).identity
    if (identity.kind, identity.schema_name, identity.schema_version) != (
        "build_run",
        "BuildRun",
        "1.0",
    ):
        raise ContractError("reuse source must be a BuildRun snapshot")
    source = read_build_run(engine.store.read_structured(snapshot))
    if source.dag is None or run.dag is None:
        raise ContractError("reuse requires DAG source")
    raw_plan = engine.store.read_structured(source.dag.plan)
    for node in plan.static_plan.nodes:
        state = source.dag.node_states.get(node.node_id)
        binding = plan.bindings[node.node_id]
        if (
            state is None
            or state.status != "succeeded"
            or binding.adapter not in REUSABLE
            or any(
                run.dag.node_states[p].status != "succeeded"
                for p in plan.static_plan.dependencies[node.node_id]
            )
        ):
            continue
        inputs = engine._inputs(run, node)
        if canonical_json_bytes(raw_plan["bindings"].get(node.node_id)) != canonical_json_bytes(
            binding.to_dict()
        ) or state.current().input_digest != engine._input_digest(plan, node, inputs):
            continue
        source_attempt = verify_source(engine, snapshot, node, plan, inputs)
        attempt = DagAttempt(
            1,
            status="succeeded",
            resolved_inputs=copy.deepcopy(inputs),
            input_digest=engine._input_digest(plan, node, inputs),
            outputs=copy.deepcopy(source_attempt.outputs),
            operator=node.operator,
            adapter=binding.adapter,
            binding_digest=digest(binding.to_dict()),
            parameters_digest=digest(binding.parameters),
            started_at=utc_now(),
            finished_at=utc_now(),
            reused_from=snapshot,
        )
        attempt.provenance = persist_node_provenance(
            engine.store,
            run_id=run.run_id,
            node_id=node.node_id,
            attempt=1,
            operator=node.operator,
            adapter_identity=binding.to_dict(),
            parameters=thaw(binding.parameters),
            inputs=inputs,
            outputs=attempt.outputs,
            execution_evidence=snapshot,
        )
        run.dag.node_states[node.node_id].attempts = [attempt]
        run.dag.node_states[node.node_id].status = "succeeded"

"""Serial, durable DAG execution with owned, gated process adapters."""

from __future__ import annotations

import copy
import uuid
from typing import Any

from .compiled_plan import CompiledNode, digest, freeze, thaw
from .contracts import ContractError, validate_operator_outputs, validate_port_value
from .dag_adapters import AdapterRegistry, BoundDagPlan, NodeExecutionContext
from .dag_evidence import index_evidence, node_evidence
from .dag_models import DagAttempt, DagNodeState, DagState, PortMap
from .dag_persistence import DagRepository, EvidenceError
from .dag_provenance import expected_node_provenance_records, persist_node_provenance
from .errors import classify_error
from .models import ArtifactRef, BuildRun, NodeAttempt, StructuredValue
from .pipeline import _operator_specs_from_raw, _port_spec
from .process_admission import admit_compute
from .relations import RelationValidatorRegistry, ResolvedRelation, default_relation_registry
from .runtime import utc_now
from .serialization import canonical_json_bytes, read_json, to_primitive
from .workbench_context import ChildRunContext
from .workbench_models import ChildRegistration
from .workbench_persistence import ProcessProbe, _references
from .workbench_process import LinuxProcessProbe


class DagEngine:
    def __init__(
        self,
        repository: DagRepository,
        registry: AdapterRegistry,
        relations: RelationValidatorRegistry | None = None,
        probe: ProcessProbe | None = None,
    ):
        self.repository = repository
        self.store = repository.store
        self.registry = registry
        self.relations = relations or default_relation_registry()
        self.probe = probe or LinuxProcessProbe()

    def _plan(self, run: BuildRun) -> BoundDagPlan:
        assert run.dag is not None
        for state in run.dag.node_states.values():
            for attempt in state.attempts:
                if attempt.remote_binding is not None and not attempt.adapter:
                    raise ContractError("remote attempt scheduling is not enabled without adapter")
        self.repository.verify_reference_closure(run.dag.plan)
        manifest = self.store.get_manifest(run.dag.plan.artifact_id)
        if (
            manifest.identity.kind != "dag_plan"
            or manifest.identity.schema_name != "BoundDagPlan"
            or manifest.identity.schema_version != "1.0"
        ):
            raise ContractError("invalid plan artifact contract")
        plan = BoundDagPlan.from_dict(
            self.store.read_structured(run.dag.plan),
            registry=self.registry,
            relation_registry=self.relations,
        )
        if (
            plan.plan_id != run.dag.plan_id
            or set(run.dag.node_states) != set(plan.bindings)
            or run.pipeline_name != plan.static_plan.pipeline_name
            or run.pipeline_version != plan.static_plan.pipeline_version
        ):
            raise ContractError("run plan or node set mismatch")
        for node_id, state in run.dag.node_states.items():
            if any(attempt.remote_binding is not None for attempt in state.attempts):
                if plan.bindings[node_id].spec["execution_kind"] != "remote":
                    raise ContractError(
                        "remote attempt scheduling is not enabled for local adapters"
                    )
        return plan

    def create(
        self,
        plan: BoundDagPlan,
        inputs: PortMap,
        run_id: str | None = None,
        reuse_source: ArtifactRef | None = None,
    ) -> BuildRun:
        with self.repository._command_lock:
            plan = BoundDagPlan.from_dict(
                plan.to_dict(), registry=self.registry, relation_registry=self.relations
            )
            if set(inputs) - set(plan.static_plan.inputs):
                raise ContractError("unknown pipeline inputs")
            for name, port in plan.static_plan.inputs.items():
                validate_port_value(
                    operator=plan.static_plan.pipeline_name,
                    port_name=name,
                    spec=_port_spec(thaw(port.contract)),
                    value=inputs.get(name),
                    store=self.store,
                )
            self._verify_values(inputs)
            reference = self.store.persist_structured(
                StructuredValue("dag_plan", "BoundDagPlan", "1.0", plan.to_dict())
            )
            run = BuildRun(
                run_id or f"dag_{uuid.uuid4().hex}",
                plan.static_plan.pipeline_name,
                plan.static_plan.pipeline_version,
                "running",
                {},
                [],
                utc_now(),
                None,
                dag=DagState(
                    reference,
                    plan.plan_id,
                    copy.deepcopy(inputs),
                    {node.node_id: DagNodeState(node.node_id) for node in plan.static_plan.nodes},
                ),
            )
            if reuse_source is not None:
                from .dag_reuse import seed_reuse

                seed_reuse(self, run, plan, reuse_source)
            self.repository.create(run)
            return run

    def _verify_values(self, values: Any) -> None:
        for reference in _references(to_primitive(values)):
            self.repository.verify_reference_closure(reference)

    def _save(self, run: BuildRun) -> None:
        assert run.dag is not None
        run.node_attempts = [
            NodeAttempt(
                node.node_id,
                attempt.attempt,
                attempt.operator,
                None,
                attempt.status,
                "cached" if attempt.reused_from is not None else "executed",
                attempt.started_at,
                attempt.finished_at,
                attempt.error_code,
                copy.deepcopy(attempt.outputs),
            )
            for node in run.dag.node_states.values()
            for attempt in node.attempts
        ]
        index_evidence(run.dag, self.repository)
        self.repository.save(run, expected_revision=run.dag.revision)

    def _inputs(self, run: BuildRun, node: CompiledNode) -> PortMap:
        assert run.dag is not None
        values: PortMap = {}
        for name, binding in node.inputs.items():
            if binding.source == "pipeline_input":
                source = run.dag.named_actual_inputs
            else:
                parent = run.dag.node_states[str(binding.node_id)]
                if parent.status != "succeeded":
                    raise ContractError("dependency is not a verified success")
                source = parent.current().outputs
            if binding.port in source:
                values[name] = copy.deepcopy(source[binding.port])
            elif not binding.optional:
                raise ContractError(f"required binding unavailable: {binding.reference}")
        for name, contract in node.operator_contract["inputs"].items():
            if name not in values and contract.get("cardinality", "one") in {
                "zero_or_more",
                "many",
            }:
                values[name] = []
        return values

    def _input_digest(
        self,
        plan: BoundDagPlan,
        node: CompiledNode,
        inputs: PortMap,
        decision: ArtifactRef | None = None,
    ) -> str:
        return digest(
            {
                "inputs": to_primitive(inputs),
                "operator": node.operator_contract_digest,
                "binding": plan.bindings[node.node_id].to_dict(),
                "relations": thaw(node.relations),
                "decision": to_primitive(decision),
            }
        )

    def _validate_inputs(self, node: CompiledNode, inputs: PortMap) -> None:
        self._verify_values(inputs)
        spec = _operator_specs_from_raw({"operators": [thaw(node.operator_contract)]})[
            node.operator
        ]
        relations = tuple(
            ResolvedRelation(item["name"], item["version"], item["digest"], tuple(item["inputs"]))
            for item in node.relations
        )
        self.relations.validate_runtime(spec, inputs, self.store, resolved_relations=relations)

    def _validate_outputs(self, node: CompiledNode, outputs: PortMap) -> None:
        spec = _operator_specs_from_raw({"operators": [thaw(node.operator_contract)]})[
            node.operator
        ]
        validate_operator_outputs(spec, outputs, self.store)
        self._verify_values(outputs)

    def _block(self, run: BuildRun, state: DagNodeState, code: str, error: Exception) -> None:
        assert run.dag is not None
        state.status = "recovery_blocked"
        state.recovery_blocked_reason = f"{code}: {error}"
        if isinstance(error, EvidenceError):
            run.dag.invalid_evidence[error.artifact_id] = str(error)

    def _human_evidence(
        self, run: BuildRun, plan: BoundDagPlan, node: CompiledNode, attempt: DagAttempt
    ) -> None:
        human = plan.bindings[node.node_id].spec["execution_kind"] == "human"
        if not human:
            if attempt.request is not None or attempt.decision is not None:
                raise ContractError("CPU attempt cannot carry human evidence")
            return
        if attempt.request is None:
            raise ContractError("human attempt requires its original request")
        self._verify_values(attempt.request)
        manifest = self.store.get_manifest(attempt.request.artifact_id)
        request = self.store.read_structured(attempt.request)
        original_digest = self._input_digest(plan, node, attempt.resolved_inputs)
        if (
            manifest.identity.kind != "dag_human_request"
            or manifest.identity.schema_name != "DagHumanInputRequest"
            or manifest.identity.schema_version != "1.0"
            or any(
                request.get(key) != value
                for key, value in {
                    "run_id": run.run_id,
                    "node_id": node.node_id,
                    "input_digest": original_digest,
                }.items()
            )
        ):
            raise ContractError("human request binding mismatch")
        assert run.dag is not None
        origins = {
            f"{run.run_id}/{node.node_id}/{item.attempt}"
            for item in run.dag.node_states[node.node_id].attempts
            if item.attempt <= attempt.attempt and item.request == attempt.request
        }
        if request.get("attempt_id") not in origins:
            raise ContractError("human request attempt identity mismatch")
        if attempt.decision is None:
            if attempt.status == "succeeded":
                raise ContractError("human success requires a decision")
            return
        self._verify_values(attempt.decision)
        manifest = self.store.get_manifest(attempt.decision.artifact_id)
        decision = self.store.read_structured(attempt.decision)
        if (
            manifest.identity.kind != "dag_human_decision"
            or manifest.identity.schema_name != "DagHumanDecision"
            or manifest.identity.schema_version != "1.0"
            or any(
                decision.get(key) != value
                for key, value in {
                    "run_id": run.run_id,
                    "node_id": node.node_id,
                    "input_digest": original_digest,
                    "request": to_primitive(attempt.request),
                }.items()
            )
            or not isinstance(decision.get("reviewer"), str)
            or not decision["reviewer"].strip()
            or not isinstance(decision.get("payload"), dict)
        ):
            raise ContractError("human decision binding mismatch")

    def _resume_prepared_decisions(self, run: BuildRun, plan: BoundDagPlan) -> None:
        """Finish decisions whose idempotency receipt was durable before the artifact.

        The prepared receipt is deliberately written in its own snapshot.  A crash
        after the decision artifact is published but before the next snapshot can
        therefore be recovered deterministically without creating a second decision.
        """
        assert run.dag is not None
        for key, receipt in list(run.dag.receipts.items()):
            if receipt.get("status", "committed") != "prepared":
                continue
            node_id = receipt.get("node_id")
            if not isinstance(node_id, str) or node_id not in run.dag.node_states:
                raise ContractError("prepared decision references an unknown node")
            node = next(item for item in plan.static_plan.nodes if item.node_id == node_id)
            state = run.dag.node_states[node_id]
            if not state.attempts or state.status != "waiting_for_input":
                raise ContractError("prepared decision node is no longer waiting")
            attempt = state.current()
            reviewer = receipt.get("reviewer")
            payload = receipt.get("payload")
            if (
                not isinstance(reviewer, str)
                or not reviewer.strip()
                or not isinstance(payload, dict)
            ):
                raise ContractError("malformed prepared decision receipt")
            if "reuse" in receipt:
                from .dag_decision_reuse import decision_proposal

                proposal = decision_proposal(
                    self, run, node_id, ArtifactRef(**receipt["reuse"]["source_snapshot"])
                )
                if canonical_json_bytes(proposal) != canonical_json_bytes(receipt["reuse"]):
                    raise ContractError("prepared decision reuse evidence changed")
            decision = self.store.persist_structured(
                StructuredValue(
                    "dag_human_decision",
                    "DagHumanDecision",
                    "1.0",
                    {
                        "run_id": run.run_id,
                        "node_id": node_id,
                        "request": to_primitive(attempt.request),
                        "input_digest": attempt.input_digest,
                        "reviewer": reviewer,
                        "payload": copy.deepcopy(payload),
                        **(
                            {
                                "reuse": copy.deepcopy(receipt["reuse"]),
                                "confirmed_at": receipt["confirmed_at"],
                            }
                            if "reuse" in receipt
                            else {}
                        ),
                    },
                )
            )
            attempt.decision = decision
            attempt.input_digest = self._input_digest(plan, node, attempt.resolved_inputs, decision)
            attempt.status = state.status = "running"
            run.dag.receipts[key] = {
                **receipt,
                "status": "committed",
                "decision": to_primitive(decision),
                "attempt": attempt.attempt,
            }

    def _recover(self, run: BuildRun, plan: BoundDagPlan) -> None:
        assert run.dag is not None
        self._resume_prepared_decisions(run, plan)
        nodes = {node.node_id: node for node in plan.static_plan.nodes}
        index_evidence(run.dag, self.repository)
        known_invalid = set(run.dag.invalid_evidence)
        # Detect every missing historical reference before publishing a block. The
        # exemption is an audit fact, never permission to consume that evidence.
        pending = _references(to_primitive(run))
        invalid_child_nodes: set[str] = set()
        for state in run.dag.node_states.values():
            for attempt in state.attempts:
                if attempt.child_registration is not None:
                    try:
                        reference = self.repository.child_reference(attempt)
                        pending.append(reference)
                        self.repository.verify_reference_closure(reference)
                    except (OSError, ValueError, KeyError) as error:
                        self._block(run, state, "recovery_child_invalid", error)
                        invalid_child_nodes.add(state.node_id)
        seen: set[str] = set()
        while pending:
            ref = pending.pop()
            if ref.artifact_id in seen:
                continue
            seen.add(ref.artifact_id)
            try:
                self.repository.verify_reference_closure(ref)
            except EvidenceError as error:
                run.dag.invalid_evidence[error.artifact_id] = str(error)
            try:
                if self.store.verify_digest(ref):
                    pending.extend(self.repository._children(ref))
            except (OSError, ValueError, KeyError):
                pass
        for state in run.dag.node_states.values():
            if (
                state.status not in {"failed", "interrupted", "recovery_blocked"}
                or not state.attempts
            ):
                continue
            try:
                self._verify_values(to_primitive(state.current()))
            except Exception as error:
                self._block(run, state, "recovery_historical_evidence_invalid", error)
        for node_id, node in nodes.items():
            state = run.dag.node_states[node_id]
            if state.status in {"pending", "recovery_blocked"} and not state.attempts:
                try:
                    external = {
                        name: run.dag.named_actual_inputs[binding.port]
                        for name, binding in node.inputs.items()
                        if binding.source == "pipeline_input"
                        and binding.port in run.dag.named_actual_inputs
                    }
                    self._verify_values(external)
                    if state.status == "recovery_blocked":
                        state.status = "pending"
                        state.recovery_blocked_reason = None
                except Exception as error:
                    self._block(run, state, "recovery_dependency_invalid", error)
        for node_id in plan.static_plan.topological_order:
            state = run.dag.node_states[node_id]
            if node_id in invalid_child_nodes:
                continue
            if state.dispatch_block_reason:
                try:
                    admit_compute(self.repository, self.probe)
                except ContractError as error:
                    state.dispatch_block_reason = str(error)
                else:
                    state.dispatch_block_reason = None
            if any(
                worker.launch_phase not in {"prepared", "identity_recorded", "exit_observed"}
                for attempt in state.attempts
                for worker in attempt.worker_executions
            ):
                try:
                    admit_compute(self.repository, self.probe)
                except ContractError as error:
                    state.dispatch_block_reason = str(error)
                else:
                    state.dispatch_block_reason = None
            if plan.bindings[node_id].spec["execution_kind"] == "remote" and state.attempts:
                if (
                    state.status in {"running", "interrupted", "recovery_blocked"}
                    and state.current().status != "succeeded"
                ):
                    from .dag_remote_execution import execute_remote

                    execute_remote(self, run, plan, nodes[node_id], recover=True)
                    continue
            if state.status == "running":
                state.status = "interrupted"
                state.current().status = "interrupted"
                state.current().error_code = "execution_interrupted"
                state.current().finished_at = utc_now()
            if (
                state.status in {"interrupted", "failed"}
                and state.attempts
                and state.current().child_registration is not None
                and not state.dispatch_block_reason
            ):
                node = nodes[node_id]
                attempt = state.current()
                adapter = self.registry.resolve(plan.bindings[node_id])
                if callable(getattr(adapter, "recover", None)):
                    try:
                        current_inputs = self._inputs(run, node)
                        self._validate_inputs(node, current_inputs)
                        if canonical_json_bytes(current_inputs) != canonical_json_bytes(
                            attempt.resolved_inputs
                        ) or attempt.input_digest != self._input_digest(
                            plan, node, current_inputs, attempt.decision
                        ):
                            raise ContractError("child recovery input binding mismatch")
                        binding = plan.bindings[node_id]
                        if (
                            attempt.operator != node.operator
                            or attempt.adapter != binding.adapter
                            or attempt.binding_digest != digest(binding.to_dict())
                            or attempt.parameters_digest != digest(binding.parameters)
                        ):
                            raise ContractError("child recovery implementation binding mismatch")
                        self._execute(run, plan, node, recover_child=True)
                    except Exception as error:
                        self._block(run, state, "recovery_child_invalid", error)
            if state.status not in {"succeeded", "waiting_for_input", "recovery_blocked"}:
                continue
            if not state.attempts:
                continue
            attempt = state.current()
            node = nodes[node_id]
            if (state.recovery_blocked_reason or "").startswith(
                "recovery_historical_evidence_invalid: historical evidence invalid"
            ):
                try:
                    for artifact_id in set(run.dag.invalid_evidence) & node_evidence(
                        run.dag, node_id
                    ):
                        self.repository.verify_reference_closure(ArtifactRef(artifact_id))
                except EvidenceError:
                    continue  # Repair evidence or explicitly retry to acknowledge this audit.
            code = "recovery_dependency_invalid"
            try:
                if attempt.status in {"failed", "interrupted"}:
                    self._verify_values(to_primitive(attempt))
                inputs = self._inputs(run, node)
                self._validate_inputs(node, inputs)
                code = "recovery_input_mismatch"
                if canonical_json_bytes(inputs) != canonical_json_bytes(
                    attempt.resolved_inputs
                ) or attempt.input_digest != self._input_digest(
                    plan, node, inputs, attempt.decision
                ):
                    raise ContractError("resolved input evidence differs from plan")
                binding = plan.bindings[node_id]
                if (
                    attempt.operator != node.operator
                    or attempt.adapter != binding.adapter
                    or attempt.binding_digest != digest(binding.to_dict())
                    or attempt.parameters_digest != digest(binding.parameters)
                ):
                    raise ContractError("recorded implementation identity differs from plan")
                self._human_evidence(run, plan, node, attempt)
                if attempt.child_registration is not None:
                    child_reference = self.repository.child_reference(attempt)
                    self.repository.verify_reference_closure(child_reference)
                if attempt.status == "succeeded" and binding.spec.get("uses_child_run"):
                    registration = attempt.child_registration
                    if registration is None or attempt.child_result is None:
                        raise ContractError("child workflow success requires durable evidence")
                    self.repository.verify_reference_closure(attempt.child_result)
                    child = self.store.read_structured(attempt.child_result)
                    if (
                        child.get("run_id") != registration.child_run_id
                        or child.get("parent_run_id") != run.run_id
                        or child.get("status") != "succeeded"
                    ):
                        raise ContractError("child result binding mismatch")
                code = "recovery_output_invalid"
                if attempt.status == "succeeded":
                    if attempt.reused_from is not None:
                        from .dag_reuse import verify_source

                        original = verify_source(self, attempt.reused_from, node, plan, inputs)
                        if canonical_json_bytes(original.outputs) != canonical_json_bytes(
                            attempt.outputs
                        ):
                            raise ContractError("reused output mismatch")
                    elif binding.spec["execution_kind"] == "remote":
                        from .dag_remote_execution import validate_remote_success

                        validate_remote_success(self, run, node)
                    self._validate_outputs(node, attempt.outputs)
                    self._verify_values(attempt.provenance)
                    expected = expected_node_provenance_records(
                        self.store,
                        run_id=run.run_id,
                        node_id=node_id,
                        attempt=attempt.attempt,
                        operator=node.operator,
                        adapter_identity=plan.bindings[node_id].to_dict(),
                        parameters=thaw(plan.bindings[node_id].parameters),
                        inputs=inputs,
                        outputs=attempt.outputs,
                        decision_ref=attempt.decision,
                        execution_evidence=attempt.reused_from or attempt.remote_result,
                    )
                    actual = {
                        port: [self.store.read_structured(ref) for ref in refs]
                        for port, refs in attempt.provenance.items()
                    }
                    if canonical_json_bytes(expected) != canonical_json_bytes(actual):
                        raise ContractError("output provenance differs from execution evidence")
                if state.status == "recovery_blocked":
                    state.status = attempt.status
                    state.recovery_blocked_reason = None
            except Exception as error:
                self._block(run, state, code, error)
        # Newly discovered historical corruption blocks its actual owners, even
        # if their current attempts no longer reference the damaged artifact.
        for artifact_id in set(run.dag.invalid_evidence) - known_invalid:
            owners = {
                item.node_id
                for item in run.dag.evidence_consumers.get(artifact_id, [])
                if item.node_id is not None
            }
            for owner in owners:
                state = run.dag.node_states[owner]
                if state.status != "recovery_blocked":
                    self._block(
                        run,
                        state,
                        "recovery_historical_evidence_invalid",
                        ContractError(
                            "historical evidence invalid; inspect invalid_evidence and retry"
                        ),
                    )
            if not owners:
                run.dag.unassigned_evidence_blocks[artifact_id] = run.dag.invalid_evidence[
                    artifact_id
                ]
        # Unused inputs and legacy unlocatable evidence belong to the run,
        # never an arbitrary first node. They clear only after evidence repair.
        for artifact_id in list(run.dag.unassigned_evidence_blocks):
            try:
                self.repository.verify_reference_closure(ArtifactRef(artifact_id))
            except EvidenceError:
                continue
            del run.dag.unassigned_evidence_blocks[artifact_id]
        self._aggregate(run, plan)

    def _aggregate(self, run: BuildRun, plan: BoundDagPlan) -> None:
        assert run.dag is not None
        states = run.dag.node_states
        for node_id in plan.static_plan.topological_order:
            state = states[node_id]
            if state.status not in {"pending", "blocked"}:
                continue
            parents = [states[key].status for key in plan.static_plan.dependencies[node_id]]
            state.status = (
                "blocked"
                if any(
                    item in {"failed", "interrupted", "blocked", "recovery_blocked"}
                    for item in parents
                )
                else "pending"
            )
        statuses = {state.status for state in states.values()}
        ready = any(
            state.status == "pending"
            and not state.dispatch_block_reason
            and all(
                states[parent].status == "succeeded"
                for parent in plan.static_plan.dependencies[key]
            )
            for key, state in states.items()
        )
        if "running" in statuses or ready:
            run.status = "running"
        elif (
            "recovery_blocked" in statuses
            or run.dag.unassigned_evidence_blocks
            or any(state.dispatch_block_reason for state in states.values())
        ):
            run.status = "recovery_blocked"
        elif "waiting_for_input" in statuses:
            run.status = "waiting_for_input"
        elif statuses <= {"succeeded"}:
            run.status = "succeeded"
        elif "interrupted" in statuses:
            run.status = "interrupted"
        else:
            run.status = "failed"
        run.finished_at = (
            (run.finished_at or utc_now()) if run.status in {"succeeded", "failed"} else None
        )

    def recover(self, run_id: str) -> BuildRun:
        with self.repository._command_lock:
            run = self.repository.load(run_id)
            plan = self._plan(run)
            self._recover(run, plan)
            self._save(run)
            return run

    def drain(self, run_id: str) -> BuildRun:
        with self.repository._command_lock:
            run = self.repository.load(run_id)
            plan = self._plan(run)
            self._recover(run, plan)
            self._save(run)
            assert run.dag is not None
            for node in plan.static_plan.nodes:
                state = run.dag.node_states[node.node_id]
                if state.status != "pending" or any(
                    run.dag.node_states[parent].status != "succeeded"
                    for parent in plan.static_plan.dependencies[node.node_id]
                ):
                    continue
                inputs = self._inputs(run, node)
                binding = plan.bindings[node.node_id]
                if binding.spec["execution_kind"] == "process":
                    try:
                        self._validate_inputs(node, inputs)
                        admit_compute(self.repository, self.probe)
                    except ContractError as error:
                        state.dispatch_block_reason = str(error)
                        self._aggregate(run, plan)
                        self._save(run)
                        continue
                    state.dispatch_block_reason = None
                attempt = DagAttempt(
                    len(state.attempts) + 1,
                    resolved_inputs=inputs,
                    input_digest=self._input_digest(plan, node, inputs),
                    operator=node.operator,
                    adapter=binding.adapter,
                    binding_digest=digest(binding.to_dict()),
                    parameters_digest=digest(binding.parameters),
                    started_at=utc_now(),
                )
                if state.attempts and state.current().decision is not None:
                    previous = state.current()
                    if canonical_json_bytes(previous.resolved_inputs) == canonical_json_bytes(
                        inputs
                    ):
                        try:
                            self._human_evidence(run, plan, node, previous)
                        except (ValueError, OSError):
                            # Invalid old evidence requires a fresh human decision.
                            pass
                        else:
                            attempt.request = previous.request
                            attempt.decision = previous.decision
                            attempt.input_digest = self._input_digest(
                                plan, node, inputs, attempt.decision
                            )
                state.attempts.append(attempt)
                state.status = "running"
                self._save(run)
                self._execute(run, plan, node)
            self._aggregate(run, plan)
            self._save(run)
            return run

    def _child_context(self, run: BuildRun, node: CompiledNode) -> ChildRunContext:
        assert run.dag is not None
        attempt = run.dag.node_states[node.node_id].current()
        if attempt.child_reservation is None:
            child_id = f"run_{uuid.uuid4().hex}"
            attempt.child_reservation = ChildRegistration(
                child_id,
                run.run_id,
                node.node_id,
                attempt.attempt,
                attempt.input_digest,
                str(self.store.root / "run_owners" / f"{child_id}.json"),
            )
            self._save(run)
        reservation = attempt.child_reservation
        created = self.repository.register_child(run, reservation)
        index = self.store.root / "runs" / f"{reservation.child_run_id}.json"
        if created or not index.exists():
            child = BuildRun(
                reservation.child_run_id,
                node.operator.split("@")[0],
                node.operator.split("@")[1],
                "running",
                {},
                [],
                utc_now(),
                None,
                parent_run_id=run.run_id,
            )
            self.repository.commit(child, owner=reservation)
        attempt.child_registration = reservation
        self._save(run)
        return ChildRunContext(self.repository, reservation)

    def _execute(
        self, run: BuildRun, plan: BoundDagPlan, node: CompiledNode, *, recover_child: bool = False
    ) -> None:
        assert run.dag is not None
        if plan.bindings[node.node_id].spec["execution_kind"] == "remote":
            from .dag_remote_execution import execute_remote

            execute_remote(self, run, plan, node, recover=False)
            return
        state = run.dag.node_states[node.node_id]
        attempt = state.current()
        binding = plan.bindings[node.node_id]
        try:
            self._validate_inputs(node, attempt.resolved_inputs)
            if attempt.decision is not None:
                self._human_evidence(run, plan, node, attempt)
            adapter = self.registry.resolve(binding)
            from .dag_worker import DagProcessWorker

            worker = (
                DagProcessWorker(self, run, node.node_id)
                if binding.spec["execution_kind"] == "process"
                else None
            )
            child_context = None
            output_path = None
            if binding.spec.get("uses_child_run") and (
                binding.spec["execution_kind"] != "human" or attempt.decision is not None
            ):
                if recover_child:
                    if attempt.child_registration is None:
                        raise ContractError("child recovery requires registration")
                    child_context = ChildRunContext(self.repository, attempt.child_registration)
                else:
                    child_context = self._child_context(run, node)
                output_path = (
                    self.repository.directory
                    / "executions"
                    / run.run_id
                    / child_context.registration.child_run_id
                    / "release"
                )
            context = NodeExecutionContext(
                run.run_id,
                node.node_id,
                copy.deepcopy(attempt.resolved_inputs),
                binding.parameters,
                self.store,
                attempt.decision,
                f"{run.run_id}/{node.node_id}/{attempt.attempt}",
                self._input_digest(plan, node, attempt.resolved_inputs),
                worker,
                child_context,
                output_path,
            )
            if recover_child:
                result = adapter.recover(context)  # type: ignore[attr-defined]
                if result is None:
                    return
                if result.wait_request is not None:
                    raise ContractError("child recovery cannot create a new human request")
            else:
                result = adapter.execute(context)
            if result.wait_request is not None:
                if binding.spec["execution_kind"] != "human" or attempt.decision is not None:
                    raise ContractError("only an undecided human node can wait")
                self.repository.verify_reference_closure(result.wait_request)
                manifest = self.store.get_manifest(result.wait_request.artifact_id)
                request = self.store.read_structured(result.wait_request)
                if (
                    manifest.identity.kind != "dag_human_request"
                    or manifest.identity.schema_name != "DagHumanInputRequest"
                    or any(
                        request.get(key) != value
                        for key, value in {
                            "run_id": run.run_id,
                            "node_id": node.node_id,
                            "input_digest": attempt.input_digest,
                            "attempt_id": f"{run.run_id}/{node.node_id}/{attempt.attempt}",
                        }.items()
                    )
                ):
                    raise ContractError("human request does not bind exact run/node/inputs")
                attempt.request = result.wait_request
                attempt.status = state.status = "waiting_for_input"
            else:
                if binding.spec["execution_kind"] == "human" and attempt.decision is None:
                    raise ContractError("human node cannot succeed without explicit decision")
                outputs = copy.deepcopy(dict(result.outputs))
                if child_context is not None:
                    child_id = child_context.registration.child_run_id
                    child = self.repository.load(child_id)
                    if child.status != "succeeded" or child.parent_run_id != run.run_id:
                        raise ContractError("child workflow did not complete successfully")
                    attempt.child_result = ArtifactRef(
                        **read_json(self.store.root / "runs" / f"{child_id}.json")
                    )
                self._validate_outputs(node, outputs)
                attempt.provenance = persist_node_provenance(
                    self.store,
                    run_id=run.run_id,
                    node_id=node.node_id,
                    attempt=attempt.attempt,
                    operator=node.operator,
                    adapter_identity=binding.to_dict(),
                    parameters=thaw(binding.parameters),
                    inputs=attempt.resolved_inputs,
                    outputs=outputs,
                    decision_ref=attempt.decision,
                )
                attempt.outputs = outputs
                attempt.status = state.status = "succeeded"
                attempt.finished_at = utc_now()
        except Exception as error:
            attempt.status = state.status = "failed"
            attempt.error_code = classify_error(error).value
            attempt.error_detail = str(error)
            attempt.finished_at = utc_now()
        self._aggregate(run, plan)
        self._save(run)

    def retry(self, run_id: str, node_id: str, expected_revision: int) -> BuildRun:
        with self.repository._command_lock:
            run = self.repository.load(run_id)
            assert run.dag is not None
            if run.dag.revision != expected_revision:
                raise ContractError("DAG revision conflict")
            plan = self._plan(run)
            self._recover(run, plan)
            state = run.dag.node_states[node_id]
            if state.status not in {"failed", "interrupted", "recovery_blocked"}:
                raise ContractError("only failed/interrupted/blocked evidence nodes can retry")
            node = next(item for item in plan.static_plan.nodes if item.node_id == node_id)
            # Validate the retry boundary before resetting the node and its
            # descendants.  If evidence disappeared or a dependency is no
            # longer a verified success, persist a durable block rather than
            # returning with an in-memory-only mutation (or silently leaving
            # recovery changes unsaved).
            try:
                self._validate_inputs(node, self._inputs(run, node))
            except Exception as error:
                self._block(run, state, "retry_input_invalid", error)
                self._aggregate(run, plan)
                self._save(run)
                raise
            if plan.bindings[node_id].spec["execution_kind"] == "process":
                try:
                    admit_compute(self.repository, self.probe)
                except ContractError as error:
                    state.dispatch_block_reason = str(error)
                    self._aggregate(run, plan)
                    self._save(run)
                    raise
            affected = {node_id}
            pending = [node_id]
            while pending:
                for child in plan.static_plan.dependents[pending.pop()]:
                    if child not in affected:
                        affected.add(child)
                        pending.append(child)
            from .dag_remote_execution import validate_remote_retry

            for key in affected:
                validate_remote_retry(self, run, key)
            for key in affected:
                run.dag.node_states[key].status = "pending"
                run.dag.node_states[key].recovery_blocked_reason = None
                run.dag.node_states[key].dispatch_block_reason = None
            self._aggregate(run, plan)
            self._save(run)
            return self.drain(run_id)

    def save_draft(
        self, run_id: str, node_id: str, *, expected_revision: int, payload: dict[str, Any]
    ) -> BuildRun:
        """Persist unvalidated editing state, never an authorization to execute."""
        with self.repository._command_lock:
            run = self.repository.load(run_id)
            assert run.dag is not None
            if run.dag.revision != expected_revision:
                raise ContractError("DAG revision conflict")
            plan = self._plan(run)
            self._recover(run, plan)
            state = run.dag.node_states[node_id]
            if state.status != "waiting_for_input":
                self._save(run)
                raise ContractError("node is not waiting for input")
            state.current().draft = thaw(freeze(payload))
            self._save(run)
            return run

    def decide(
        self,
        run_id: str,
        node_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
        reviewer: str,
        payload: dict[str, Any],
        reuse_source: ArtifactRef | None = None,
    ) -> BuildRun:
        with self.repository._command_lock:
            run = self.repository.load(run_id)
            assert run.dag is not None
            if not idempotency_key or not reviewer.strip():
                raise ContractError("decision requires idempotency key and reviewer")
            body_digest = digest(
                {
                    "node_id": node_id,
                    "reviewer": reviewer,
                    "payload": payload,
                    **({"reuse_source": to_primitive(reuse_source)} if reuse_source else {}),
                }
            )
            previous = run.dag.receipts.get(idempotency_key)
            if previous is not None:
                if previous["request_digest"] != body_digest:
                    raise ContractError("decision idempotency conflict")
                if previous.get("status", "committed") == "prepared":
                    plan = self._plan(run)
                    self._resume_prepared_decisions(run, plan)
                    self._aggregate(run, plan)
                    self._save(run)
                    return self.drain(run_id)
                # Replaying a command acknowledges its original decision only.
                # Interrupted execution requires retry(), which creates an audited
                # attempt and revalidates the current input/request binding.
                return run
            if run.dag.revision != expected_revision:
                raise ContractError("DAG revision conflict")
            plan = self._plan(run)
            self._recover(run, plan)
            state = run.dag.node_states[node_id]
            if state.status != "waiting_for_input":
                self._save(run)
                raise ContractError("node is not waiting for input")
            node = next(item for item in plan.static_plan.nodes if item.node_id == node_id)
            reuse = None
            if reuse_source is not None:
                from .dag_decision_reuse import decision_proposal

                reuse = decision_proposal(self, run, node_id, reuse_source)
                if canonical_json_bytes(reuse["payload"]) != canonical_json_bytes(payload):
                    raise ContractError("confirmed payload differs from original decision")
            run.dag.receipts[idempotency_key] = {
                "request_digest": body_digest,
                "node_id": node_id,
                "reviewer": reviewer,
                "payload": copy.deepcopy(payload),
                "status": "prepared",
                **({"reuse": reuse, "confirmed_at": utc_now()} if reuse else {}),
            }
            self._save(run)
            self._resume_prepared_decisions(run, plan)
            self._save(run)
            self._execute(run, plan, node)
            return self.drain(run_id)

"""Serial, durable CPU/human DAG execution; process/GPU adapters are not admitted."""

from __future__ import annotations

import copy
import uuid
from typing import Any

from .compiled_plan import CompiledNode, digest, freeze, thaw
from .contracts import ContractError, validate_operator_outputs, validate_port_value
from .dag_adapters import AdapterRegistry, BoundDagPlan, NodeExecutionContext
from .dag_models import DagAttempt, DagNodeState, DagState, PortMap
from .dag_persistence import DagRepository, EvidenceError
from .dag_provenance import expected_node_provenance_records, persist_node_provenance
from .errors import classify_error
from .models import ArtifactRef, BuildRun, NodeAttempt, StructuredValue
from .pipeline import _operator_specs_from_raw, _port_spec
from .relations import RelationValidatorRegistry, ResolvedRelation, default_relation_registry
from .runtime import utc_now
from .serialization import canonical_json_bytes, to_primitive
from .workbench_persistence import _references


class DagEngine:
    def __init__(
        self,
        repository: DagRepository,
        registry: AdapterRegistry,
        relations: RelationValidatorRegistry | None = None,
    ):
        self.repository = repository
        self.store = repository.store
        self.registry = registry
        self.relations = relations or default_relation_registry()

    def _plan(self, run: BuildRun) -> BoundDagPlan:
        assert run.dag is not None
        self.repository.verify_reference_closure(run.dag.plan)
        if any(state.dispatch_block_reason for state in run.dag.node_states.values()):
            raise ContractError("process dispatch blocks are unsupported by the CPU-only engine")
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
        return plan

    def create(self, plan: BoundDagPlan, inputs: PortMap, run_id: str | None = None) -> BuildRun:
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
                "executed",
                attempt.started_at,
                attempt.finished_at,
                attempt.error_code,
                copy.deepcopy(attempt.outputs),
            )
            for node in run.dag.node_states.values()
            for attempt in node.attempts
        ]
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
        known_invalid = set(run.dag.invalid_evidence)
        # Detect every missing historical reference before publishing a block. The
        # exemption is an audit fact, never permission to consume that evidence.
        pending = _references(to_primitive(run))
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
            if state.status == "running":
                state.status = "interrupted"
                state.current().status = "interrupted"
                state.current().error_code = "execution_interrupted"
                state.current().finished_at = utc_now()
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
                    for artifact_id in run.dag.invalid_evidence:
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
                code = "recovery_output_invalid"
                if attempt.status == "succeeded":
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
        used_inputs = {
            binding.port
            for node in nodes.values()
            for binding in node.inputs.values()
            if binding.source == "pipeline_input"
        }
        unused = {
            key: value
            for key, value in run.dag.named_actual_inputs.items()
            if key not in used_inputs
        }
        if unused:
            try:
                self._verify_values(unused)
            except Exception as error:
                self._block(
                    run,
                    next(iter(run.dag.node_states.values())),
                    "recovery_unused_input_invalid",
                    error,
                )
        if set(run.dag.invalid_evidence) - known_invalid and not any(
            state.recovery_blocked_reason for state in run.dag.node_states.values()
        ):
            state = next(iter(run.dag.node_states.values()))
            self._block(
                run,
                state,
                "recovery_historical_evidence_invalid",
                ContractError("historical evidence invalid; inspect invalid_evidence and retry"),
            )
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
            and all(
                states[parent].status == "succeeded"
                for parent in plan.static_plan.dependencies[key]
            )
            for key, state in states.items()
        )
        if "running" in statuses or ready:
            run.status = "running"
        elif "recovery_blocked" in statuses:
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

    def _execute(self, run: BuildRun, plan: BoundDagPlan, node: CompiledNode) -> None:
        assert run.dag is not None
        state = run.dag.node_states[node.node_id]
        attempt = state.current()
        binding = plan.bindings[node.node_id]
        try:
            self._validate_inputs(node, attempt.resolved_inputs)
            if attempt.decision is not None:
                self._human_evidence(run, plan, node, attempt)
            adapter = self.registry.resolve(binding)
            result = adapter.execute(
                NodeExecutionContext(
                    run.run_id,
                    node.node_id,
                    copy.deepcopy(attempt.resolved_inputs),
                    binding.parameters,
                    self.store,
                    attempt.decision,
                    f"{run.run_id}/{node.node_id}/{attempt.attempt}",
                    self._input_digest(plan, node, attempt.resolved_inputs),
                )
            )
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
            affected = {node_id}
            pending = [node_id]
            while pending:
                for child in plan.static_plan.dependents[pending.pop()]:
                    if child not in affected:
                        affected.add(child)
                        pending.append(child)
            for key in affected:
                run.dag.node_states[key].status = "pending"
                run.dag.node_states[key].recovery_blocked_reason = None
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
            run.dag.receipts[idempotency_key] = {
                "request_digest": body_digest,
                "node_id": node_id,
                "reviewer": reviewer,
                "payload": copy.deepcopy(payload),
                "status": "prepared",
            }
            self._save(run)
            self._resume_prepared_decisions(run, plan)
            self._save(run)
            self._execute(run, plan, node)
            return self.drain(run_id)

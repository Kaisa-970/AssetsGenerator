"""Pure workbench transitions. Adapters must verify artifact/result evidence before events."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TypeAlias

from .models import ArtifactRef, BuildRun, PortValue
from .serialization import cache_key
from .workbench_models import (
    ChildRegistration,
    CommandReceipt,
    ComputationInput,
    DecisionCommand,
    MaskDraft,
    ProcessIdentity,
    ProcessObservation,
    StageAttempt,
    WorkerExecution,
)


class TransitionError(ValueError):
    pass


@dataclass(frozen=True)
class PrepareStage:
    inputs: ComputationInput
    receipt: CommandReceipt | None = None
    worker: WorkerExecution | None = None


@dataclass(frozen=True)
class RetryRequested(PrepareStage):
    pass


@dataclass(frozen=True)
class ChildRegistered:
    registration: ChildRegistration


@dataclass(frozen=True)
class LauncherIdentified:
    identity: ProcessIdentity


@dataclass(frozen=True)
class AuthorizeLaunch:
    pass


@dataclass(frozen=True)
class HumanRequestReady:
    request_ref: ArtifactRef
    input_digest: str


@dataclass(frozen=True)
class MaskPreviewReady:
    request_ref: ArtifactRef
    draft: MaskDraft


@dataclass(frozen=True)
class DecisionPrepared:
    command: DecisionCommand
    receipt: CommandReceipt


@dataclass(frozen=True)
class ChildSucceeded:
    child_run_id: str
    input_digest: str
    outputs: dict[str, PortValue | list[PortValue]]
    # Explicit attestation by the result-verifying adapter, not a browser payload.
    publication_verified: bool
    restored: bool = False


@dataclass(frozen=True)
class ExecutionFailed:
    error_code: str
    observation: ProcessObservation | None = None


@dataclass(frozen=True)
class RecoveryObserved:
    observation: ProcessObservation | None
    error_code: str = "execution_interrupted"


Payload: TypeAlias = (
    PrepareStage
    | RetryRequested
    | ChildRegistered
    | LauncherIdentified
    | AuthorizeLaunch
    | HumanRequestReady
    | MaskPreviewReady
    | DecisionPrepared
    | ChildSucceeded
    | ExecutionFailed
    | RecoveryObserved
)


@dataclass(frozen=True)
class Event:
    event_id: str
    expected_revision: int
    stage_id: str
    attempt: int
    occurred_at: str
    payload: Payload


@dataclass(frozen=True)
class Effect:
    kind: str
    stage_id: str
    attempt: int


@dataclass(frozen=True)
class Transition:
    state: BuildRun
    effects: tuple[Effect, ...] = ()
    duplicate: bool = False
    result_refs: dict[str, ArtifactRef] = field(default_factory=dict)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TransitionError(message)


def _receipt(payload: Payload) -> CommandReceipt | None:
    return payload.receipt if isinstance(payload, (PrepareStage, DecisionPrepared)) else None


def _check_receipt(receipt: CommandReceipt) -> None:
    _require(
        bool(
            receipt.idempotency_key
            and receipt.request_digest
            and receipt.command_kind
            and receipt.child_run_id
            and receipt.output_location
        ),
        "incomplete receipt",
    )
    _require(receipt.status == "prepared" and not receipt.result_refs, "receipt must be prepared")


def _activity_block(attempt: StageAttempt) -> str | None:
    worker = attempt.worker_execution
    if worker is None:
        return None
    if worker.launch_phase == "prepared":
        return None  # Gate has not been authorized; an unidentified launcher cannot run a model.
    observation = worker.last_probe
    if observation is None or observation.result != "exited":
        return "old process/group is active or its exit is unverified"
    return None


def transition(current: BuildRun, event: Event) -> Transition:
    """No I/O, clock, random values or mutation of current/event objects."""
    wb = current.workbench
    _require(wb is not None, "run has no workbench state")
    assert wb is not None
    _require(bool(event.event_id and event.occurred_at), "event identity/time required")
    fingerprint = cache_key(event)
    if event.event_id in wb.event_receipts:
        _require(wb.event_receipts[event.event_id] == fingerprint, "event ID payload conflict")
        return Transition(deepcopy(current), duplicate=True)
    if isinstance(event.payload, DecisionPrepared):
        _require(
            event.payload.receipt.request_digest == cache_key(event.payload.command),
            "decision request digest mismatch",
        )
    elif isinstance(event.payload, PrepareStage) and event.payload.receipt is not None:
        _require(
            event.payload.receipt.request_digest == event.payload.inputs.digest(),
            "compute request digest mismatch",
        )
    command_receipt = _receipt(event.payload)
    if command_receipt is not None:
        _check_receipt(command_receipt)
        for prior_stage in wb.stage_states.values():
            for prior_attempt in prior_stage.attempts:
                old = prior_attempt.command_receipt
                if old and old.idempotency_key == command_receipt.idempotency_key:
                    _require(
                        old.request_digest == command_receipt.request_digest
                        and old.command_kind == command_receipt.command_kind
                        and prior_stage.stage_id == event.stage_id,
                        "idempotency key request conflict",
                    )
                    return Transition(
                        deepcopy(current), duplicate=True, result_refs=deepcopy(old.result_refs)
                    )
    _require(event.expected_revision == wb.state_revision, "stale revision")
    _require(event.stage_id in wb.stage_states, "unknown stage")
    state = deepcopy(current)
    assert state.workbench is not None
    stage = state.workbench.stage_states[event.stage_id]
    payload = deepcopy(event.payload)
    effects: list[Effect] = []

    def effect(kind: str) -> None:
        effects.append(Effect(kind, stage.stage_id, event.attempt))

    if isinstance(payload, PrepareStage):
        retry = isinstance(payload, RetryRequested)
        _require(
            stage.status in ({"failed", "interrupted"} if retry else {"pending"}),
            "stage cannot be prepared in this state",
        )
        _require(event.attempt == len(stage.attempts) + 1, "attempt must increase by one")
        for previous in state.workbench.stage_states.values():
            if previous.stage_id == stage.stage_id:
                break
            _require(previous.status == "succeeded", "upstream stage has not succeeded")
        old_ids = {a.child_run_id for s in wb.stage_states.values() for a in s.attempts}
        _require(
            payload.receipt is None or payload.receipt.child_run_id not in old_ids,
            "new attempt requires a new child run ID",
        )
        for other in state.workbench.stage_states.values():
            if other.stage_id != stage.stage_id:
                _require(
                    other.status not in {"running", "waiting_for_input"}, "another stage is active"
                )
        if retry:
            _require(
                stage.current().retry_blocked_reason is None
                and _activity_block(stage.current()) is None,
                "retry blocked by old process",
            )
            old_ids = {a.child_run_id for s in wb.stage_states.values() for a in s.attempts}
            _require(
                payload.receipt is None or payload.receipt.child_run_id not in old_ids,
                "retry requires a new child run ID",
            )
        _require(stage.human or payload.receipt is not None, "compute stage requires receipt")
        if payload.worker is not None:
            _require(
                payload.receipt is not None
                and payload.worker.child_run_id == payload.receipt.child_run_id
                and payload.worker.launch_phase == "prepared",
                "invalid prepared worker",
            )
        attempt = StageAttempt(
            stage.stage_id,
            event.attempt,
            payload.inputs.adapter,
            None,
            "running",
            "executed",
            event.occurred_at,
            None,
            None,
            resolved_inputs=payload.inputs.named_actual_inputs,
            input_digest=payload.inputs.digest(),
            child_run_id=payload.receipt.child_run_id if payload.receipt else None,
            command_receipt=payload.receipt,
            worker_execution=payload.worker,
        )
        stage.attempts.append(attempt)
        stage.active_attempt = event.attempt
        stage.status = "running"
        stage.request_ref = None
        stage.draft = None
        effect("prepare_human_request" if stage.human else "register_child")
    else:
        attempt = stage.current()
        _require(event.attempt == attempt.attempt, "old attempt event")
        _require(stage.status != "succeeded", "successful output is immutable")
        if isinstance(payload, ChildRegistered):
            reg = payload.registration
            _require(
                stage.status == "running" and attempt.command_receipt is not None,
                "child requires prepared receipt",
            )
            _require(
                (reg.parent_run_id, reg.stage_id, reg.attempt, reg.input_digest, reg.child_run_id)
                == (
                    state.run_id,
                    stage.stage_id,
                    attempt.attempt,
                    attempt.input_digest,
                    attempt.child_run_id,
                ),
                "child ownership mismatch",
            )
            _require(attempt.child_registration is None, "child already registered")
            attempt.child_registration = reg
            effect("start_launcher" if attempt.worker_execution else "execute_adapter")
        elif isinstance(payload, LauncherIdentified):
            worker = attempt.worker_execution
            _require(
                stage.status == "running"
                and attempt.child_registration is not None
                and worker is not None
                and worker.launch_phase == "prepared",
                "launcher requires registered child",
            )
            assert worker is not None
            identity = payload.identity
            worker.host_id, worker.boot_id = identity.host_id, identity.boot_id
            worker.pid, worker.starttime_ticks, worker.pgid = (
                identity.pid,
                identity.starttime_ticks,
                identity.pgid,
            )
            worker.launch_phase = "identity_recorded"
        elif isinstance(payload, AuthorizeLaunch):
            worker = attempt.worker_execution
            _require(
                stage.status == "running"
                and attempt.child_registration is not None
                and worker is not None
                and worker.launch_phase == "identity_recorded",
                "authorization requires durable registration and identity",
            )
            assert worker is not None
            worker.identity()
            worker.launch_phase = "release_authorized"
            effect("release_launcher")
        elif isinstance(payload, HumanRequestReady):
            _require(
                stage.human
                and stage.status == "running"
                and attempt.command_receipt is None
                and payload.input_digest == attempt.input_digest,
                "invalid human request",
            )
            stage.request_ref = payload.request_ref
            stage.status = "waiting_for_input"
            attempt.status = "waiting_for_input"
        elif isinstance(payload, MaskPreviewReady):
            _require(
                stage.status == "waiting_for_input" and stage.request_ref == payload.request_ref,
                "preview request mismatch",
            )
            stage.draft = payload.draft
        elif isinstance(payload, DecisionPrepared):
            _require(
                stage.human
                and stage.status == "waiting_for_input"
                and stage.request_ref == payload.command.request_ref
                and stage.draft == payload.command.draft,
                "decision must bind current preview",
            )
            _require(
                payload.receipt.request_digest == cache_key(payload.command),
                "decision request digest mismatch",
            )
            attempt.input_digest = cache_key(
                {"request_input": attempt.input_digest, "decision": payload.command}
            )
            attempt.resolved_inputs["request"] = payload.command.request_ref
            attempt.resolved_inputs["final_mask"] = payload.command.draft.final_mask
            attempt.command_receipt = payload.receipt
            attempt.child_run_id = payload.receipt.child_run_id
            stage.status = "running"
            attempt.status = "running"
            effect("register_child")
        elif isinstance(payload, ChildSucceeded):
            _require(
                stage.status in {"running", "interrupted"}
                and attempt.child_registration is not None
                and payload.child_run_id == attempt.child_run_id
                and payload.input_digest == attempt.input_digest
                and payload.publication_verified
                and bool(payload.outputs),
                "success requires verified child execution/publication",
            )
            attempt.outputs = payload.outputs
            attempt.execution_mode = "restored" if payload.restored else "executed"
            attempt.finished_at = event.occurred_at
            attempt.error_code = None
            attempt.retry_blocked_reason = None
            stage.status = "succeeded"
            if attempt.command_receipt:
                attempt.command_receipt.status = "completed"
                attempt.command_receipt.result_refs = {
                    key: value
                    for key, value in payload.outputs.items()
                    if isinstance(value, ArtifactRef)
                }
        elif isinstance(payload, (ExecutionFailed, RecoveryObserved)):
            _require(
                stage.status in {"running", "waiting_for_input", "failed", "interrupted"},
                "stage not recoverable",
            )
            if (
                isinstance(payload, RecoveryObserved)
                and stage.human
                and attempt.command_receipt is None
            ):
                if stage.request_ref is None:
                    stage.status = "running"
                    effect("prepare_human_request")
                else:
                    stage.status = "waiting_for_input"
            else:
                if attempt.worker_execution:
                    attempt.worker_execution.last_probe = payload.observation
                    if payload.observation and payload.observation.result == "exited":
                        attempt.worker_execution.launch_phase = "exit_observed"
                attempt.retry_blocked_reason = _activity_block(attempt)
                attempt.error_code = payload.error_code
                attempt.finished_at = event.occurred_at
                stage.status = "failed" if isinstance(payload, ExecutionFailed) else "interrupted"
                if attempt.command_receipt and isinstance(payload, ExecutionFailed):
                    attempt.command_receipt.status = "failed"
        else:
            raise TransitionError("unsupported event")
        attempt.status = stage.status
    _update_run_status(state, event.occurred_at)
    state.workbench.state_revision += 1
    state.workbench.event_receipts[event.event_id] = fingerprint
    return Transition(state, tuple(effects))


def _update_run_status(run: BuildRun, occurred_at: str) -> None:
    assert run.workbench is not None
    states = [stage.status for stage in run.workbench.stage_states.values()]
    if states and all(status == "succeeded" for status in states):
        run.status, run.finished_at = "succeeded", occurred_at
    elif "interrupted" in states or "failed" in states:
        run.status = "interrupted" if "interrupted" in states else "failed"
        run.finished_at = occurred_at
    else:
        run.status = "waiting_for_input" if "waiting_for_input" in states else "running"
        run.finished_at = None

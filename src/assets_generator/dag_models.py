"""Typed durable generic-DAG execution evidence, independent of adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from .models import ArtifactRef, PortValue
from .workbench_models import ChildRegistration, WorkerExecution

PortMap: TypeAlias = dict[str, PortValue | list[PortValue]]


@dataclass
class DagAttempt:
    attempt: int
    status: str = "running"
    resolved_inputs: PortMap = field(default_factory=dict)
    input_digest: str = ""
    outputs: PortMap = field(default_factory=dict)
    operator: str = ""
    adapter: str = ""
    binding_digest: str = ""
    parameters_digest: str = ""
    started_at: str = ""
    finished_at: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    request: ArtifactRef | None = None
    draft: dict[str, Any] | None = None
    decision: ArtifactRef | None = None
    provenance: dict[str, list[ArtifactRef]] = field(default_factory=dict)
    worker_executions: list[WorkerExecution] = field(default_factory=list)
    child_reservation: ChildRegistration | None = None
    child_registration: ChildRegistration | None = None
    child_result: ArtifactRef | None = None

    def __post_init__(self) -> None:
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("DAG attempt index must be positive")
        if self.status not in {
            "running",
            "waiting_for_input",
            "succeeded",
            "failed",
            "interrupted",
        }:
            raise ValueError("invalid DAG attempt status")


@dataclass
class DagNodeState:
    node_id: str
    status: str = "pending"
    attempts: list[DagAttempt] = field(default_factory=list)
    recovery_blocked_reason: str | None = None
    dispatch_block_reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {
            "pending",
            "running",
            "waiting_for_input",
            "succeeded",
            "failed",
            "interrupted",
            "blocked",
            "recovery_blocked",
        }:
            raise ValueError("invalid DAG node status")
        for attempt in self.attempts:
            attempt.__post_init__()
            reservation = attempt.child_reservation
            registration = attempt.child_registration
            if reservation is not None and (
                reservation.stage_id != self.node_id
                or reservation.attempt != attempt.attempt
                or reservation.input_digest != attempt.input_digest
            ):
                raise ValueError("child reservation must match node/attempt/input binding")
            if registration is not None and registration != reservation:
                raise ValueError("child registration must match reservation")
            if attempt.child_result is not None and registration is None:
                raise ValueError("child result requires registration")
        if [attempt.attempt for attempt in self.attempts] != list(range(1, len(self.attempts) + 1)):
            raise ValueError("DAG attempt indices must be contiguous from one")

        # These two states are derived control states rather than execution
        # attempts.  Keep their durable representation unambiguous: a
        # recovery block must explain what evidence prevented recovery, while
        # a dependency block must not masquerade as an attempt that ran.
        if self.status == "recovery_blocked":
            if (
                not isinstance(self.recovery_blocked_reason, str)
                or not self.recovery_blocked_reason.strip()
            ):
                raise ValueError("recovery_blocked node requires a reason")
        elif self.recovery_blocked_reason is not None:
            raise ValueError("recovery_blocked_reason is only valid for recovery_blocked nodes")

        if self.status == "blocked" and self.attempts and self.current().status == "running":
            raise ValueError("blocked node cannot contain a running attempt")

        if self.status in {"running", "waiting_for_input", "succeeded", "failed", "interrupted"}:
            if not self.attempts or self.current().status != self.status:
                raise ValueError("DAG node status must match its current attempt")

    def current(self) -> DagAttempt:
        if not self.attempts:
            raise ValueError(f"node {self.node_id} has no attempt")
        return self.attempts[-1]


@dataclass(frozen=True)
class DagEvidenceConsumer:
    node_id: str | None
    attempt: int | None
    role: str
    port: str | None

    def __post_init__(self) -> None:
        if not self.role or (
            self.attempt is not None and (type(self.attempt) is not int or self.attempt < 1)
        ):
            raise ValueError("invalid evidence consumer")


@dataclass
class DagState:
    plan: ArtifactRef
    plan_id: str
    named_actual_inputs: PortMap
    node_states: dict[str, DagNodeState]
    revision: int = 0
    receipts: dict[str, dict[str, Any]] = field(default_factory=dict)
    invalid_evidence: dict[str, str] = field(default_factory=dict)
    schema_version: Literal["1.0"] = "1.0"
    evidence_consumers: dict[str, list[DagEvidenceConsumer]] = field(default_factory=dict)
    unassigned_evidence_blocks: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("DAG revision must be a nonnegative integer")
        if any(key != node.node_id for key, node in self.node_states.items()):
            raise ValueError("DAG node identity mismatch")
        for consumers in self.evidence_consumers.values():
            for consumer in consumers:
                consumer.__post_init__()
                if consumer.node_id is not None and consumer.node_id not in self.node_states:
                    raise ValueError("unknown evidence consumer node")
        if any(not reason for reason in self.unassigned_evidence_blocks.values()):
            raise ValueError("unassigned evidence block requires reason")

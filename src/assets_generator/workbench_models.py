"""Versioned workbench records; no execution, filesystem or model dependencies."""

from __future__ import annotations

import dataclasses
import types
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from .models import ArtifactRef, BuildRun, NodeAttempt, PortValue
from .serialization import cache_key, to_primitive

if TYPE_CHECKING:
    pass

Status = Literal["pending", "running", "waiting_for_input", "succeeded", "failed", "interrupted"]


@dataclass(frozen=True)
class InputBinding:
    input_name: str | None = None
    stage_id: str | None = None
    output_port: str | None = None

    def __post_init__(self) -> None:
        if not (
            (self.input_name and not self.stage_id and not self.output_port)
            or (not self.input_name and self.stage_id and self.output_port)
        ):
            raise ValueError("binding must name an imported input or a stage output")


@dataclass(frozen=True)
class PlanStage:
    stage_id: str
    adapter: str
    adapter_version: str
    inputs: dict[str, InputBinding]
    parameters: dict[str, Any]
    human: bool = False


@dataclass(frozen=True)
class WorkbenchPlan:
    input_refs: dict[str, ArtifactRef]
    stages: list[PlanStage]
    contract_digests: dict[str, str]
    backend_bindings: dict[str, Any]
    child_plan: dict[str, Any]
    schema_version: Literal["1.0"] = "1.0"
    template: Literal["photo_object_asset"] = "photo_object_asset"
    template_version: Literal["1"] = "1"

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for stage in self.stages:
            if not stage.stage_id or stage.stage_id in seen:
                raise ValueError("duplicate or empty stage ID")
            for binding in stage.inputs.values():
                if binding.input_name is not None and binding.input_name not in self.input_refs:
                    raise ValueError("unknown imported input")
                if binding.stage_id is not None and binding.stage_id not in seen:
                    raise ValueError(
                        "stages must be topologically ordered; unknown/forward dependency"
                    )
            seen.add(stage.stage_id)


@dataclass(frozen=True)
class MaskDraft:
    proposal_id: str
    final_mask: ArtifactRef
    invert: bool = False
    keep_largest: bool = False
    rule_version: Literal["mask-edit-v1"] = "mask-edit-v1"
    operation_order: tuple[str, str] = ("invert", "keep_largest")
    connectivity: Literal[8] = 8
    tie_break: Literal["row_major_first"] = "row_major_first"

    def __post_init__(self) -> None:
        if not self.proposal_id or self.operation_order != ("invert", "keep_largest"):
            raise ValueError("invalid mask editing contract")


@dataclass(frozen=True)
class HumanInputRequest:
    run_id: str
    stage_id: str
    attempt: int
    input_refs: dict[str, ArtifactRef]
    review_evidence_digest: str
    schema_version: Literal["1.0"] = "1.0"
    decision_contract: Literal["single-proposal-mask-edit@1"] = "single-proposal-mask-edit@1"
    allowed_actions: tuple[str, ...] = ("select_one", "invert", "keep_largest")


@dataclass(frozen=True)
class DecisionCommand:
    request_ref: ArtifactRef
    draft: MaskDraft
    reviewer: str
    reused_from: ArtifactRef | None = None

    def __post_init__(self) -> None:
        if not self.reviewer.strip():
            raise ValueError("reviewer must not be blank")


@dataclass
class CommandReceipt:
    idempotency_key: str
    request_digest: str
    command_kind: str
    status: Literal["prepared", "completed", "failed"] = "prepared"
    child_run_id: str | None = None
    output_location: str | None = None
    result_refs: dict[str, ArtifactRef] = field(default_factory=dict)


@dataclass(frozen=True)
class ChildRegistration:
    child_run_id: str
    parent_run_id: str
    stage_id: str
    attempt: int
    input_digest: str
    registration_location: str


@dataclass(frozen=True)
class ProcessIdentity:
    host_id: str
    boot_id: str
    pid: int
    starttime_ticks: int
    pgid: int

    def __post_init__(self) -> None:
        if (
            not self.host_id
            or not self.boot_id
            or min(self.pid, self.starttime_ticks, self.pgid) <= 0
        ):
            raise ValueError("incomplete process identity")


@dataclass(frozen=True)
class ProcessObservation:
    observed_at: str
    result: Literal["alive", "exited", "unknown"]
    observed_identity: ProcessIdentity | None = None
    group_members: tuple[ProcessIdentity, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.result == "exited" and self.group_members:
            raise ValueError("exited requires an empty process group")


@dataclass
class WorkerExecution:
    job_id: str
    child_run_id: str
    launch_request_digest: str
    launch_phase: Literal[
        "prepared", "identity_recorded", "release_authorized", "exit_observed"
    ] = "prepared"
    host_id: str | None = None
    boot_id: str | None = None
    pid: int | None = None
    starttime_ticks: int | None = None
    pgid: int | None = None
    exit_code: int | None = None
    last_probe: ProcessObservation | None = None

    def identity(self) -> ProcessIdentity:
        if (
            self.host_id is None
            or self.boot_id is None
            or self.pid is None
            or self.starttime_ticks is None
            or self.pgid is None
        ):
            raise ValueError("worker identity has not been recorded")
        return ProcessIdentity(
            self.host_id, self.boot_id, self.pid, self.starttime_ticks, self.pgid
        )


@dataclass
class StageAttempt(NodeAttempt):
    error_detail: str | None = None
    resolved_inputs: dict[str, PortValue | list[PortValue]] = field(default_factory=dict)
    input_digest: str = ""
    child_run_id: str | None = None
    retry_blocked_reason: str | None = None
    command_receipt: CommandReceipt | None = None
    child_registration: ChildRegistration | None = None
    worker_execution: WorkerExecution | None = None


@dataclass
class StageState:
    stage_id: str
    human: bool = False
    status: Status = "pending"
    active_attempt: int | None = None
    attempts: list[StageAttempt] = field(default_factory=list)
    request_ref: ArtifactRef | None = None
    draft: MaskDraft | None = None

    def current(self) -> StageAttempt:
        if not self.attempts or self.active_attempt != self.attempts[-1].attempt:
            raise ValueError("stage has no current attempt")
        return self.attempts[-1]


@dataclass
class WorkbenchState:
    plan_ref: ArtifactRef
    stage_states: dict[str, StageState]
    schema_version: Literal["1.0"] = "1.0"
    state_revision: int = 0
    stage_order: list[str] = field(default_factory=list)
    # Transport deduplication only; execution facts remain in attempts.
    event_receipts: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.stage_order and len(self.stage_states) == 1:
            self.stage_order = list(self.stage_states)
        if len(self.stage_order) != len(set(self.stage_order)) or set(self.stage_order) != set(
            self.stage_states
        ):
            raise ValueError("stage_order must explicitly contain every stage exactly once")


@dataclass(frozen=True)
class ComputationInput:
    adapter: str
    version: str
    contract_digest: str
    named_actual_inputs: dict[str, PortValue | list[PortValue]]
    parameters: dict[str, Any]
    backend_identity: dict[str, Any]

    def digest(self) -> str:
        return cache_key(self)


def proposal_evidence_digest(
    *,
    image: ArtifactRef,
    proposals: list[dict[str, Any]],
    backend_identity: dict[str, Any],
    amg_parameters: dict[str, Any],
    decision_contract: str = "single-proposal-mask-edit@1",
) -> str:
    """Explicit evidence projection. Provenance and materialization paths are not UI facts.

    Callers provide resolved content identities, never profile paths, for backend_identity.
    Required candidate fields are indexed (missing evidence is not silently omitted).
    """
    fields = (
        "proposal_id",
        "mask",
        "bbox",
        "area",
        "label",
        "source",
        "predicted_iou",
        "stability_score",
    )
    return cache_key(
        {
            "projection": "proposal-review-evidence-v1",
            "image": image,
            "proposals": [{key: proposal[key] for key in fields} for proposal in proposals],
            "backend_identity": backend_identity,
            "amg_parameters": amg_parameters,
            "decision_contract": decision_contract,
        }
    )


T = TypeVar("T")


def decode_record(cls: type[T], value: dict[str, Any]) -> T:
    """Strict typed decoding for versioned records, including nested PortValues."""
    result = _decode(cls, value)
    if not isinstance(result, cls):
        raise ValueError("record type mismatch")
    return result


def _decode(annotation: Any, value: Any) -> Any:
    if annotation is Any:
        return value
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, types.UnionType):
        for option in args:
            try:
                return _decode(option, value)
            except (TypeError, ValueError):
                continue
        raise ValueError("invalid union value")
    if origin is Literal:
        if not any(type(value) is type(item) and value == item for item in args):
            raise ValueError("unsupported enum/schema value")
        return value
    if origin in (list, tuple):
        if not isinstance(value, (tuple, list)):
            raise ValueError("expected sequence")
        if origin is tuple and len(args) > 1 and args[-1] is not Ellipsis:
            if len(args) != len(value):
                raise ValueError("tuple length mismatch")
            return tuple(_decode(t, item) for t, item in zip(args, value, strict=True))
        items = [_decode(args[0], item) for item in value]
        return tuple(items) if origin is tuple else items
    if origin is dict:
        if not isinstance(value, dict):
            raise ValueError("expected mapping")
        return {_decode(args[0], key): _decode(args[1], item) for key, item in value.items()}
    if dataclasses.is_dataclass(annotation):
        if not isinstance(value, dict):
            raise ValueError("expected record")
        from .dag_models import DagState

        hints = (
            get_type_hints(
                annotation, localns={"WorkbenchState": WorkbenchState, "DagState": DagState}
            )
            if annotation is BuildRun
            else get_type_hints(annotation)
        )
        if set(value) - set(hints):
            raise ValueError("unknown record fields")
        return cast(Any, annotation)(
            **{key: _decode(hints[key], item) for key, item in value.items()}
        )
    if type(value) is not annotation:
        raise ValueError("primitive type mismatch")
    return value


def read_build_run(value: dict[str, Any]) -> BuildRun:
    from .dag_models import DagState

    # BuildRun keeps its extension annotation lazy to avoid a models import cycle.
    raw = dict(value)
    extension = raw.pop("workbench", None)
    dag_extension = raw.pop("dag", None)
    if extension is not None and dag_extension is not None:
        raise ValueError("BuildRun cannot contain both workbench and dag state")
    run = decode_record(BuildRun, raw)
    if extension is not None:
        run.workbench = decode_record(WorkbenchState, extension)
    if dag_extension is not None:
        run.dag = decode_record(DagState, dag_extension)
    return run


def record_payload(value: Any) -> dict[str, Any]:
    raw = to_primitive(value)
    if not isinstance(raw, dict):
        raise ValueError("expected record")
    return raw

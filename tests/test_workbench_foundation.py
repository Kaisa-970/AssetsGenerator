from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from assets_generator.artifact_store import ArtifactStoreError, LocalArtifactStore
from assets_generator.models import ArtifactRef, BuildRun, StructuredValue
from assets_generator.serialization import cache_key, to_primitive
from assets_generator.workbench_models import (
    ChildRegistration,
    CommandReceipt,
    ComputationInput,
    DecisionCommand,
    HumanInputRequest,
    InputBinding,
    MaskDraft,
    PlanStage,
    ProcessIdentity,
    ProcessObservation,
    StageState,
    WorkbenchPlan,
    WorkbenchState,
    WorkerExecution,
    decode_record,
    proposal_evidence_digest,
    read_build_run,
)
from assets_generator.workbench_persistence import CreationReceipt, WorkbenchRepository
from assets_generator.workbench_state import (
    AuthorizeLaunch,
    ChildRegistered,
    ChildSucceeded,
    DecisionPrepared,
    Event,
    ExecutionFailed,
    HumanRequestReady,
    LauncherIdentified,
    MaskPreviewReady,
    PrepareStage,
    RecoveryObserved,
    RetryRequested,
    TransitionError,
    transition,
)

REF = ArtifactRef("sha256:" + "a" * 64)
IDENTITY = ProcessIdentity("host", "boot", 123, 456, 123)
INPUT = ComputationInput("adapter", "1", "contract", {"image": REF}, {}, {"model": "digest"})


def run(*, human: bool = False, plan: ArtifactRef = REF) -> BuildRun:
    return BuildRun(
        "parent",
        "photo_object_asset",
        "1",
        "running",
        {},
        [],
        "t0",
        None,
        workbench=WorkbenchState(plan, {"stage": StageState("stage", human)}),
    )


def receipt(key: str = "key", child: str = "child", digest: str | None = None) -> CommandReceipt:
    return CommandReceipt(
        key,
        digest or INPUT.digest(),
        "execute",
        child_run_id=child,
        output_location="/controlled/output",
    )


def event(
    state: BuildRun, payload: object, *, attempt: int = 1, event_id: str | None = None
) -> Event:
    assert state.workbench is not None
    return Event(
        event_id or f"e{state.workbench.state_revision}",
        state.workbench.state_revision,
        "stage",
        attempt,
        "time",
        payload,
    )  # type: ignore[arg-type]


def advance(state: BuildRun, payload: object, *, attempt: int = 1) -> BuildRun:
    return transition(state, event(state, payload, attempt=attempt)).state


def prepared(*, worker: bool = True) -> BuildRun:
    return advance(
        run(),
        PrepareStage(
            INPUT, receipt(), WorkerExecution("job", "child", "launch") if worker else None
        ),
    )


def registered(state: BuildRun) -> BuildRun:
    assert state.workbench is not None
    attempt = state.workbench.stage_states["stage"].current()
    return advance(
        state,
        ChildRegistered(
            ChildRegistration(
                "child", "parent", "stage", 1, attempt.input_digest, "/owned/child.json"
            )
        ),
    )


def authorized() -> BuildRun:
    return advance(advance(registered(prepared()), LauncherIdentified(IDENTITY)), AuthorizeLaunch())


def test_legacy_and_nested_run_roundtrip() -> None:
    legacy = to_primitive(run())
    legacy.pop("workbench")
    legacy.pop("parent_run_id")
    old = read_build_run(legacy)
    assert old.workbench is None and old.parent_run_id is None
    state = authorized()
    assert read_build_run(to_primitive(state)) == state
    damaged = to_primitive(state)
    damaged["workbench"]["schema_version"] = "2.0"
    with pytest.raises(ValueError):
        read_build_run(damaged)


def test_plan_and_request_roundtrip_and_dependency_rejection() -> None:
    plan = WorkbenchPlan(
        {"image": REF},
        [PlanStage("stage", "adapter", "1", {"image": InputBinding(input_name="image")}, {})],
        {},
        {},
        {},
    )
    assert decode_record(WorkbenchPlan, to_primitive(plan)) == plan
    request = HumanInputRequest("parent", "stage", 1, {"image": REF}, "digest")
    assert decode_record(HumanInputRequest, to_primitive(request)) == request
    with pytest.raises(ValueError, match="dependency"):
        replace(
            plan,
            stages=[
                PlanStage(
                    "stage",
                    "adapter",
                    "1",
                    {"image": InputBinding(stage_id="stage", output_port="x")},
                    {},
                )
            ],
        )


def test_pure_state_sequence_and_old_event_rejection() -> None:
    initial = run()
    e = event(initial, PrepareStage(INPUT, receipt(), WorkerExecution("job", "child", "launch")))
    outcome = transition(initial, e)
    assert initial.workbench is not None and initial.workbench.state_revision == 0
    assert outcome.effects[0].kind == "register_child"
    assert transition(outcome.state, e).duplicate
    with pytest.raises(TransitionError, match="conflict"):
        transition(outcome.state, replace(e, occurred_at="different"))
    with pytest.raises(TransitionError, match="stale"):
        transition(outcome.state, replace(e, event_id="new", payload=AuthorizeLaunch()))
    with pytest.raises(TransitionError, match="authorization"):
        advance(outcome.state, AuthorizeLaunch())
    state = registered(outcome.state)
    state = advance(state, LauncherIdentified(IDENTITY))
    authorized_result = transition(state, event(state, AuthorizeLaunch()))
    assert [e.kind for e in authorized_result.effects] == ["release_launcher"]
    with pytest.raises(TransitionError, match="authorization"):
        advance(authorized_result.state, AuthorizeLaunch())
    with pytest.raises(TransitionError, match="old attempt"):
        advance(authorized_result.state, ExecutionFailed("failed"), attempt=2)


@pytest.mark.parametrize("result", ["alive", "unknown"])
def test_active_or_unknown_process_blocks_retry(result: str) -> None:
    observed = ProcessObservation("time", result, IDENTITY)  # type: ignore[arg-type]
    state = advance(authorized(), RecoveryObserved(observed))
    with pytest.raises(TransitionError, match="retry blocked"):
        advance(state, RetryRequested(INPUT, receipt("retry", "child2")), attempt=2)
    state = advance(state, RecoveryObserved(ProcessObservation("later", "exited")))
    state = advance(state, RetryRequested(INPUT, receipt("retry", "child2")), attempt=2)
    assert state.workbench is not None
    attempts = state.workbench.stage_states["stage"].attempts
    assert len(attempts) == 2 and attempts[0].status == "interrupted"
    with pytest.raises(TransitionError, match="old attempt"):
        advance(state, ChildSucceeded("child", INPUT.digest(), {"release": REF}, True))


def test_success_requires_registration_and_verified_publication() -> None:
    with pytest.raises(TransitionError, match="verified"):
        advance(prepared(), ChildSucceeded("child", INPUT.digest(), {"release": REF}, True))
    state = registered(prepared(worker=False))
    with pytest.raises(TransitionError, match="verified"):
        advance(state, ChildSucceeded("child", INPUT.digest(), {"release": REF}, False))
    state = advance(state, ChildSucceeded("child", INPUT.digest(), {"release": REF}, True, True))
    assert state.status == "succeeded"
    with pytest.raises(TransitionError, match="immutable"):
        advance(state, ExecutionFailed("late"))
    duplicate = transition(
        state, Event("retry-http", 0, "stage", 1, "t", PrepareStage(INPUT, receipt()))
    )
    assert duplicate.duplicate and duplicate.result_refs == {"release": REF}


def test_human_draft_recovery_and_idempotent_decision() -> None:
    state = advance(run(human=True), PrepareStage(INPUT))
    outcome = transition(state, event(state, RecoveryObserved(None)))
    assert outcome.effects[0].kind == "prepare_human_request"
    state = advance(outcome.state, HumanRequestReady(REF, INPUT.digest()))
    draft = MaskDraft("proposal", REF, True, True)
    state = advance(state, MaskPreviewReady(REF, draft))
    state = read_build_run(to_primitive(state))
    state = advance(state, RecoveryObserved(None))
    assert state.status == "waiting_for_input" and state.finished_at is None
    command = DecisionCommand(REF, draft, "reviewer")
    payload = DecisionPrepared(command, receipt(digest=cache_key(command)))
    state = advance(state, payload)
    duplicate = transition(state, Event("duplicate", 0, "stage", 1, "t", payload))
    assert duplicate.duplicate and duplicate.effects == ()
    changed = replace(command, reviewer="other")
    with pytest.raises(TransitionError, match="conflict"):
        advance(state, DecisionPrepared(changed, receipt(digest=cache_key(changed))))
    assert state.workbench is not None
    assert state.workbench.stage_states["stage"].current().input_digest != INPUT.digest()


def test_evidence_projection_excludes_only_execution_fields() -> None:
    proposal = dict(
        proposal_id="p",
        mask=to_primitive(REF),
        bbox=[0, 0, 2, 2],
        area=4,
        label="unknown",
        source="estimated",
        predicted_iou=0.8,
        stability_score=0.9,
    )

    def digest(items: list[dict[str, object]]) -> str:
        return proposal_evidence_digest(
            image=REF,
            proposals=items,
            backend_identity={"model": "d"},
            amg_parameters={"points_per_side": 8},
        )

    assert digest([proposal]) == digest([{**proposal, "provenance": "different", "run_id": "r"}])
    assert digest([proposal]) != digest([{**proposal, "area": 5}])
    assert digest([proposal]) != digest([{**proposal, "stability_score": 0.8}])


def make_store(tmp_path: Path) -> tuple[LocalArtifactStore, ArtifactRef]:
    store = LocalArtifactStore(tmp_path / "store")
    reference = store.persist_structured(
        StructuredValue("workbench_plan", "WorkbenchPlan", "1.0", {})
    )
    return store, reference


def test_creation_reservation_child_ownership_and_legacy_writer(tmp_path: Path) -> None:
    store, reference = make_store(tmp_path)
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        first = CreationReceipt("key", "digest", "parent")
        assert repo.reserve_creation(first) == first
        assert repo.reserve_creation(replace(first, run_id="unused")) == first
        with pytest.raises(ValueError, match="conflict"):
            repo.reserve_creation(replace(first, request_digest="changed"))
        state = advance(
            run(plan=reference),
            PrepareStage(
                replace(INPUT, named_actual_inputs={}),
                receipt(digest=replace(INPUT, named_actual_inputs={}).digest()),
            ),
        )
        repo.commit(state)
        assert state.workbench is not None
        attempt = state.workbench.stage_states["stage"].current()
        reg = ChildRegistration(
            "child",
            "parent",
            "stage",
            1,
            attempt.input_digest,
            str(store.root / "run_owners" / "child.json"),
        )
        assert repo.register_child(state, reg)
        assert not repo.register_child(state, reg)
        with pytest.raises(ArtifactStoreError, match="reserved"):
            store.record_build_run("child", StructuredValue("build_run", "BuildRun", "1.0", {}))
        child = BuildRun("child", "test", "1", "running", {}, [], "t", None, parent_run_id="parent")
        with pytest.raises(ValueError, match="owner"):
            repo.commit(child)
        repo.commit(child, owner=reg)
        assert repo.load("child") == child
        with pytest.raises(BlockingIOError):
            with WorkbenchRepository(store, tmp_path / "workbench"):
                pass


class Channel:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def identify(self) -> ProcessIdentity:
        return IDENTITY

    def release(self) -> None:
        raise AssertionError("must not release on commit failure")


@pytest.mark.parametrize("operation", ["write", "file_fsync", "publish", "directory_fsync"])
@pytest.mark.parametrize("location", ["blobs", "manifests", "runs"])
def test_commit_failures_never_release(tmp_path: Path, operation: str, location: str) -> None:
    store, reference = make_store(tmp_path)
    state = authorized()
    assert state.workbench is not None
    state.workbench.plan_ref = reference
    attempt = state.workbench.stage_states["stage"].current()
    attempt.resolved_inputs = {}
    assert attempt.worker_execution is not None
    attempt.worker_execution.launch_phase = "identity_recorded"
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        registration = ChildRegistration(
            "child",
            "parent",
            "stage",
            1,
            attempt.input_digest,
            str(store.root / "run_owners" / "child.json"),
        )
        attempt.child_registration = None
        repo.commit(state)
        repo.register_child(state, registration)
        child = BuildRun("child", "test", "1", "running", {}, [], "t", None, parent_run_id="parent")
        repo.commit(child, owner=registration)
        attempt.child_registration = registration
        repo.commit(state)

        def fail(op: str, path: Path) -> None:
            if op == operation and location in path.parts:
                raise OSError("injected")

        repo.io.failpoint = fail
        effects: list[object] = []
        channel = Channel()
        with pytest.raises(OSError, match="injected"):
            repo.apply("parent", event(state, AuthorizeLaunch()), effects.append, channel=channel)
        assert not effects and channel.closed
        with pytest.raises(RuntimeError, match="healthy"):
            repo.apply("parent", event(state, AuthorizeLaunch()), effects.append)


def test_effect_reads_already_committed_revision(tmp_path: Path) -> None:
    store, reference = make_store(tmp_path)
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        state = run(human=True, plan=reference)
        repo.commit(state)
        revisions: list[int] = []

        def effect(_: object) -> None:
            saved = repo.load("parent")
            assert saved.workbench is not None
            revisions.append(saved.workbench.state_revision)

        repo.apply(
            "parent", event(state, PrepareStage(replace(INPUT, named_actual_inputs={}))), effect
        )
        assert revisions == [1]


def test_unknown_fields_and_process_group_evidence_rejected() -> None:
    with pytest.raises(ValueError, match="unknown"):
        decode_record(MaskDraft, {**to_primitive(MaskDraft("p", REF)), "ignored": True})
    with pytest.raises(ValueError, match="empty process group"):
        ProcessObservation("t", "exited", IDENTITY, (IDENTITY,))
    with pytest.raises(ValueError, match="primitive"):
        decode_record(ProcessIdentity, {**to_primitive(IDENTITY), "pid": True})


def test_failed_commit_at_new_directory_fsync_poison_writer(tmp_path: Path) -> None:
    store, reference = make_store(tmp_path)
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:

        def fail(operation: str, path: Path) -> None:
            if operation == "directory_fsync" and path.name == "requests":
                raise OSError("directory creation sync failed")

        repo.io.failpoint = fail
        with pytest.raises(OSError):
            repo.reserve_creation(CreationReceipt("key", "digest", "parent"))
        assert not (store.root / "runs" / "parent.json").exists()
        with pytest.raises(RuntimeError):
            repo.commit(run(plan=reference))


def test_two_commands_at_one_revision_only_one_commits(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    store, reference = make_store(tmp_path)
    with WorkbenchRepository(store, tmp_path / "workbench") as repo:
        state = run(human=True, plan=reference)
        repo.commit(state)
        command = event(state, PrepareStage(replace(INPUT, named_actual_inputs={})))
        effects: list[object] = []

        def apply(identifier: str) -> bool:
            try:
                repo.apply("parent", replace(command, event_id=identifier), effects.append)
                return True
            except TransitionError:
                return False

        with ThreadPoolExecutor(2) as executor:
            results = list(executor.map(apply, ["first", "second"]))
        assert sorted(results) == [False, True]
        assert len(effects) == 1

from copy import deepcopy
from types import SimpleNamespace

import pytest

from assets_generator.dag_models import DagAttempt, DagNodeState, DagState
from assets_generator.models import ArtifactRef, BuildRun
from assets_generator.node_editor_actions import project_run_actions
from assets_generator.serialization import to_primitive


def run_with(status, reason=None):
    attempts = (
        []
        if status in {"pending", "blocked", "recovery_blocked"}
        else [DagAttempt(1, status=status)]
    )
    state = DagNodeState("node", status, attempts, recovery_blocked_reason=reason)
    return BuildRun(
        "dag_actions",
        "test",
        "1",
        "running",
        {},
        [],
        "",
        None,
        dag=DagState(ArtifactRef("plan"), "plan", {}, {"node": state}, revision=3),
    )


@pytest.mark.parametrize(
    "status",
    [
        "pending",
        "running",
        "waiting_for_input",
        "succeeded",
        "failed",
        "interrupted",
        "blocked",
        "recovery_blocked",
    ],
)
def test_projection_does_not_authorize_execution_or_modify_run(status):
    run = run_with(
        status, "recovery_output_invalid: missing" if status == "recovery_blocked" else None
    )
    before = deepcopy(to_primitive(run))
    result = project_run_actions(run)
    assert result["run_id"] == run.run_id and result["revision"] == 3
    assert result["execution_authorized"] is False
    assert result["nodes"]["node"]["retry"]["can_request"] == (
        status in {"failed", "interrupted", "recovery_blocked"}
    )
    assert to_primitive(run) == before


@pytest.mark.parametrize(
    "reason,word",
    [
        ("retry_input_invalid", "输入"),
        ("recovery_dependency_invalid", "上游"),
        ("recovery_input_mismatch", "固定计划"),
        ("recovery_output_invalid", "输出"),
        ("recovery_historical_evidence_invalid", "历史"),
        ("recovery_child_invalid", "子运行"),
        ("unknown_future_reason", "待核实"),
    ],
)
def test_guidance_preserves_reason_without_changing_request_authority(reason, word):
    run = run_with("recovery_blocked", reason + ": evidence missing")
    node = project_run_actions(run)["nodes"]["node"]
    assert word in node["guidance"]
    assert node["recorded_reasons"]["recovery_blocked_reason"] == reason + ": evidence missing"
    assert node["retry"]["eligibility"] == "requires_command_validation"


def test_busy_is_global_request_block_not_process_or_remote_proof():
    run = run_with("failed")
    result = project_run_actions(run, command_busy=True)
    assert result["resume"]["can_request"] is False
    assert result["nodes"]["node"]["retry"]["reason_code"] == "editor_busy"


def test_remote_binding_and_dispatch_record_never_imply_safe_resubmission():
    run = run_with("failed")
    state = run.dag.node_states["node"]
    # Projection must not call any remote binding/client functionality.
    state.current().remote_binding = SimpleNamespace()
    state.current().error_code = "remote_transport_unknown"
    state.dispatch_block_reason = "old process may still be alive"
    node = project_run_actions(run)["nodes"]["node"]
    assert node["remote_job_bound"] is True
    assert node["remote_state_verified"] is False
    assert "原作业" in node["guidance"] and "强制重提" in node["guidance"]
    assert node["recorded_reasons"]["dispatch_block_reason"] == state.dispatch_block_reason
    assert node["retry"]["eligibility"] == "requires_command_validation"


def test_release_failure_is_not_labelled_generation_failure():
    run = run_with("failed")
    run.dag.node_states["node"].current().error_code = "release_failed"
    assert "发布失败不等于生成失败" in project_run_actions(run)["nodes"]["node"]["guidance"]


def test_non_dag_run_rejected():
    run = run_with("failed")
    run.dag = None
    with pytest.raises(ValueError, match="DAG"):
        project_run_actions(run)

"""Read-only command guidance from a persisted run, never execution authorization.

The caller must validate editor ownership and load the run first. This module has
no repository, adapter, probe or network dependency. The command endpoint remains
authoritative for revision, evidence, descendants, process and remote job checks.
"""

from __future__ import annotations

from typing import Any

from .contracts import ContractError
from .models import BuildRun

_RECOVERY_GUIDANCE = {
    "retry_input_invalid": "先核对并修复缺失输入；提交重试后仍由后端重新核验。",
    "recovery_dependency_invalid": "先处理上游证据；重试请求仍需核验输入与依赖。",
    "recovery_input_mismatch": "核对输入与固定计划的差异，再显式请求后端核验。",
    "recovery_output_invalid": "输出证据无法核实；可请求重试核验，持续失败需人工检查。",
    "recovery_historical_evidence_invalid": "先查看历史损坏证据，再显式请求核验；不保证可以重试。",
    "recovery_child_invalid": "先核对子运行记录和证据，再请求重试核验。",
}
_ERROR_GUIDANCE = {
    "backend_unavailable": "检查服务配置后再请求重试核验。",
    "backend_timeout": "确认旧推理状态、显存和输入，再显式请求重试核验。",
    "backend_failed": "查看后端日志；不能仅凭失败状态判断输入或模型质量。",
    "output_invalid": "查看输出校验错误，再请求重试核验。",
    "release_failed": "检查发布日志、磁盘与目录权限；发布失败不等于生成失败。",
    "contract_error": "检查节点绑定与参数及其端口契约。",
}


def _action(can_request: bool, reason: str, detail: str) -> dict[str, Any]:
    return {
        "can_request": can_request,
        "eligibility": "requires_command_validation" if can_request else "unavailable",
        "reason_code": reason,
        "detail": detail,
    }


def project_run_actions(run: BuildRun, *, command_busy: bool = False) -> dict[str, Any]:
    """Project request affordances; never promise a retry or model execution.

    ``command_busy`` must describe the entire editor command/review lane, not just
    this run. A false value does not prove process admission or remote readiness.
    """
    if run.dag is None:
        raise ContractError("action projection requires a DAG run")
    nodes = {}
    for node_id, state in run.dag.node_states.items():
        attempt = state.current() if state.attempts else None
        recovery = state.recovery_blocked_reason
        error_code = attempt.error_code if attempt else None
        code = recovery.split(":", 1)[0] if recovery else error_code
        guidance = _RECOVERY_GUIDANCE.get(code or "") or _ERROR_GUIDANCE.get(code or "")
        if guidance is None:
            guidance = (
                "确认旧进程已经退出，再显式请求后端核验；不提供强制继续。"
                if state.status == "interrupted"
                else "操作资格待核实；命令执行时会重新检查输入、证据和执行条件。"
            )
        if state.dispatch_block_reason:
            guidance += " 当前记录存在派发阻塞；先核对具体原因，不提供绕过检查的操作。"
        remote = bool(attempt and attempt.remote_binding is not None)
        if remote:
            guidance += (
                " 存在已绑定的远程作业；先显式恢复以核实原作业。"
                "重试仍需检查固定结果和受影响后代；状态未知不允许强制重提。"
            )
        retryable = state.status in {"failed", "interrupted", "recovery_blocked"}
        retry = (
            _action(False, "editor_busy", "另一个编辑器命令或人工决定正在执行。")
            if command_busy
            else _action(False, "node_status_ineligible", "当前记录状态不接受节点重试请求。")
            if not retryable
            else _action(
                True,
                "retry_requires_validation",
                "可提交重试核验请求；不表示已获准重新执行，命令可能拒绝。",
            )
        )
        nodes[node_id] = {
            "status": state.status,
            "attempt": attempt.attempt if attempt else None,
            "retry": retry,
            "guidance": guidance,
            "recorded_reasons": {
                "recovery_blocked_reason": recovery,
                "dispatch_block_reason": state.dispatch_block_reason,
                "error_code": error_code,
                "error_detail": attempt.error_detail if attempt else None,
            },
            "remote_job_bound": remote,
            "remote_state_verified": False,
        }
    return {
        "schema_version": "node_editor_actions@1",
        "run_id": run.run_id,
        "revision": run.dag.revision,
        "scope": "persisted_state_request_guidance",
        "execution_authorized": False,
        "resume": _action(
            not command_busy,
            "editor_busy" if command_busy else "resume_requires_validation",
            "另一个编辑器命令或人工决定正在执行。"
            if command_busy
            else "可显式请求恢复或继续；可能核实远程原作业并执行待运行节点，不会绕过执行门控。",
        ),
        "nodes": nodes,
    }

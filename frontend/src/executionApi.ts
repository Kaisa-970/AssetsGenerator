import type { RunActionAdvice } from "./NodeActionHint";
export type NodeState = {
  status: string;
  recovery_blocked_reason?: string | null;
  dispatch_block_reason?: string | null;
  attempts?: {
    request?: { artifact_id: string };
    decision?: { artifact_id: string };
    reused_from?: { artifact_id: string };
    resolved_inputs?: Record<string, unknown>;
    outputs?: Record<string, unknown>;
    provenance?: Record<string, unknown>;
    operator?: string;
    adapter?: string;
    input_digest?: string;
    binding_digest?: string;
    parameters_digest?: string;
    started_at?: string;
    finished_at?: string | null;
    error_code?: string;
    error_detail?: string;
    status?: string;
    attempt?: number;
    remote_binding?: { service_id: string; submission_key: string };
  }[];
};
export function executionFailureSummary(state: NodeState): string | undefined {
  const attempt = state.attempts?.at(-1);
  if (!attempt?.error_code && !attempt?.error_detail) return undefined;
  const detail = attempt.error_detail || "";
  if (attempt.error_code === "remote_transport_unknown")
    return "远端状态未知 · 先核实作业";
  if (
    /out of memory|CUDA_ERROR_OUT_OF_MEMORY|cudaErrorMemoryAllocation/i.test(
      detail,
    )
  )
    return "显存不足 · 调低模型分辨率或释放 GPU 后重试";
  if (attempt.error_code === "backend_failed")
    return "模型执行失败 · 查看运行记录";
  return `${attempt.error_code || "执行失败"} · 查看运行记录`;
}
export type Run = {
  run_id: string;
  status: string;
  dag?: {
    plan_id?: string;
    named_actual_inputs?: Record<string, { artifact_id: string }>;
    revision: number;
    invalid_evidence?: Record<string, string>;
    unassigned_evidence_blocks?: Record<string, string>;
    node_states: Record<string, NodeState>;
  };
};
export type Envelope = {
  actions?: RunActionAdvice;
  snapshot_ref?: { artifact_id: string };
  run: Run;
  busy?: boolean;
  error?: string | null;
  outputs?: { node_id: string; port: string; kind?: string; url: string }[];
};
export async function request(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
) {
  const response = await fetch(path, {
    ...(body === undefined
      ? {}
      : { method: "POST", body: JSON.stringify(body) }),
    headers: { "Content-Type": "application/json" },
    signal,
  });
  const value = await response.json();
  if (!response.ok) throw Error(value.error || `HTTP ${response.status}`);
  return value;
}

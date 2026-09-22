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

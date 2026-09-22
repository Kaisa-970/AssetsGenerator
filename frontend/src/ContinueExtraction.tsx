import { useEffect, useRef, useState } from "react";
import type { Pipeline } from "./graph";

type PreparedExtraction = {
  pipeline: Pipeline;
  input_refs: Record<string, Record<string, unknown>>;
  reuse_source: { artifact_id: string };
  preflight_digest: string;
};
type EligibleProposal = {
  eligible: true;
  reason?: string;
  pipeline: Pipeline;
  input_refs: Record<string, Record<string, unknown>>;
  reuse_source: { artifact_id: string };
  source_run_id: string;
  source_node_id: string;
  extract_node_id: string;
  preflight: {
    digest: string;
    execution_ready: boolean;
    nodes: Record<string, { status: string; reason: string; detail: string }>;
  };
};

type Proposal =
  | EligibleProposal
  | {
      eligible: false;
      reason?: string;
      pipeline?: null;
      input_refs?: null;
      preflight?: null;
      reuse_source?: { artifact_id: string };
      source_run_id?: string;
      source_node_id?: string;
    };

export function ContinueExtraction({
  runId,
  nodeId,
  snapshot,
  disabled,
  onConfirm,
}: {
  runId: string;
  nodeId: string;
  snapshot: string;
  disabled: boolean;
  onConfirm: (value: PreparedExtraction) => Promise<void>;
}) {
  const identity = JSON.stringify([runId, nodeId, snapshot]);
  const current = useRef(identity);
  current.current = identity;
  const activeRequest = useRef<AbortController | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{
    identity: string;
    proposal: Proposal;
  }>();
  const [error, setError] = useState("");
  useEffect(() => () => activeRequest.current?.abort(), [identity]);
  const proposal = result?.identity === identity ? result.proposal : undefined;
  const check = async () => {
    if (activeRequest.current) return;
    const controller = new AbortController();
    activeRequest.current = controller;
    setBusy(true);
    setError("");
    setResult(undefined);
    try {
      const response = await fetch(
        `/api/runs/${encodeURIComponent(runId)}/continue-extraction/${encodeURIComponent(nodeId)}?snapshot=${encodeURIComponent(snapshot)}`,
        { signal: controller.signal },
      );
      const value: Proposal = await response.json();
      if (controller.signal.aborted || current.current !== identity) return;
      if (!response.ok)
        throw Error(
          value.reason ||
            (value as unknown as { error: string }).error ||
            `HTTP ${response.status}`,
        );
      if (typeof value.eligible !== "boolean")
        throw Error("无法核实继续提取方案");
      if (
        (value.source_run_id !== undefined && value.source_run_id !== runId) ||
        (value.source_node_id !== undefined &&
          value.source_node_id !== nodeId) ||
        (value.reuse_source !== undefined &&
          value.reuse_source?.artifact_id !== snapshot)
      )
        throw Error("继续提取响应不属于当前运行快照");
      if (
        value.eligible &&
        (value.source_run_id !== runId ||
          value.source_node_id !== nodeId ||
          value.reuse_source?.artifact_id !== snapshot ||
          !value.pipeline ||
          !value.input_refs ||
          !value.extract_node_id ||
          typeof value.preflight?.digest !== "string" ||
          value.preflight.execution_ready !== true ||
          !value.preflight.nodes ||
          value.preflight.nodes[nodeId]?.status !== "reuse" ||
          value.preflight.nodes[value.extract_node_id]?.status !== "execute" ||
          Object.entries(value.preflight.nodes).some(
            ([id, node]) =>
              node.status !== "reuse" && id !== value.extract_node_id,
          ))
      )
        throw Error("继续提取方案与来源或执行范围不匹配，请重新检查");
      setResult({ identity, proposal: value });
    } catch (cause) {
      if (!controller.signal.aborted && current.current === identity)
        setError(String(cause));
    } finally {
      if (activeRequest.current === controller) {
        activeRequest.current = undefined;
        setBusy(false);
      }
    }
  };
  return (
    <section aria-label={`继续提取 · ${nodeId}`}>
      <button disabled={disabled || busy} onClick={() => void check()}>
        继续提取 · {nodeId}
      </button>
      <p>先检查此运行快照中的分割证据，不执行模型。</p>
      {busy && <p role="status">正在核实继续提取方案…</p>}
      {error && <p role="alert">{error}</p>}
      {proposal && !proposal.eligible && (
        <p role="alert">{proposal.reason || "当前结果不支持继续提取。"}</p>
      )}
      {proposal?.eligible && (
        <>
          <p>
            已找到可继续处理的分割结果。新运行会保留当前分割，直接执行提取；原运行不会改变。
          </p>
          <div aria-label="继续提取执行范围">
            <strong>这次会做什么</strong>
            <ul>
              {Object.entries(proposal.preflight.nodes).map(([id, node]) => (
                <li key={id}>
                  {node.status === "reuse" ? "保留" : "执行"}：
                  {id === nodeId
                    ? "分割结果"
                    : id === proposal.extract_node_id
                      ? "对象提取"
                      : "前置处理"}
                </li>
              ))}
            </ul>
          </div>
          <details>
            <summary>查看核验详情</summary>
            <p>
              来源运行：{runId}；分割节点：{nodeId}
            </p>
            {Object.entries(proposal.preflight.nodes).map(([id, node]) => (
              <p key={id}>
                {id} · {node.status === "reuse" ? "已核验可复用" : "将执行"} ·{" "}
                {node.detail}
              </p>
            ))}
            <p>启动时会再次核验；不会自动继承人工决定。</p>
          </details>
          <button
            disabled={disabled || busy}
            onClick={() => {
              const prepared = structuredClone({
                pipeline: proposal.pipeline,
                input_refs: proposal.input_refs,
                reuse_source: proposal.reuse_source,
                preflight_digest: proposal.preflight.digest,
              });
              setResult(undefined);
              void onConfirm(prepared);
            }}
          >
            确认继续提取
          </button>
        </>
      )}
    </section>
  );
}

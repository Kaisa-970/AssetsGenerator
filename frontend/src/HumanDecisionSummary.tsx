import { useEffect, useState } from "react";

export function HumanDecisionSummary({
  runId,
  nodeId,
  decisionId,
  nodeFinishedAt,
}: {
  runId: string;
  nodeId: string;
  decisionId?: string;
  nodeFinishedAt?: string | null;
}) {
  const key = `${runId}/${nodeId}/${decisionId || "none"}/${nodeFinishedAt || "running"}`;
  const [record, setRecord] = useState<{
    key: string;
    value?: {
      reviewer: string;
      payload: unknown;
      node_finished_at: string | null;
    };
    error?: string;
  }>();
  useEffect(() => {
    if (!decisionId) return;
    const abort = new AbortController();
    fetch(
      `/api/runs/${encodeURIComponent(runId)}/decisions/${encodeURIComponent(nodeId)}`,
      { signal: abort.signal, cache: "no-store" },
    )
      .then(async (response) => {
        const value = await response.json();
        if (
          !response.ok ||
          !value.recorded ||
          value.run_id !== runId ||
          value.node_id !== nodeId ||
          value.decision_ref?.artifact_id !== decisionId ||
          typeof value.reviewer !== "string"
        )
          throw Error("人工决定无法核实");
        if (!abort.signal.aborted) setRecord({ key, value });
      })
      .catch((error) => {
        if (!abort.signal.aborted) setRecord({ key, error: String(error) });
      });
    return () => abort.abort();
  }, [key, runId, nodeId, decisionId]);
  const active = record?.key === key ? record : undefined;
  return (
    <section aria-label={`人工决定 · ${nodeId}`}>
      <strong>人工决定</strong>
      {!decisionId ? (
        <p>未记录人工决定；不表示质量通过。</p>
      ) : active?.error ? (
        <p role="alert">{active.error}</p>
      ) : active?.value ? (
        <>
          <p>
            来源：{runId} / {nodeId}
          </p>
          <p>选择人（自报身份，未认证）：{active.value.reviewer}</p>
          <p>确认时间：未记录</p>
          <p>节点完成时间：{active.value.node_finished_at || "未记录"}</p>
          <details>
            <summary>选择内容（不代表质量结论）</summary>
            <pre>{JSON.stringify(active.value.payload, null, 2)}</pre>
          </details>
        </>
      ) : (
        <p>正在核实决定证据…</p>
      )}
    </section>
  );
}

import { newRequestId } from "./requestId";
import { useRef, useState } from "react";
import { request } from "./executionApi";
type Proposal = {
  source_snapshot: { artifact_id: string };
  source_run_id: string;
  node_id: string;
  source_decision: { artifact_id: string };
  source_reviewer: string;
  payload: unknown;
};
export function ReuseHumanDecision({
  runId,
  nodeId,
  revision,
  runs,
  disabled,
  mutate,
}: {
  runId: string;
  nodeId: string;
  revision: number;
  runs: { run_id: string; status: string }[];
  disabled: boolean;
  mutate: (action: () => Promise<void>) => Promise<void>;
}) {
  const [source, setSource] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [checked, setChecked] = useState<{ key: string; proposal: Proposal }>();
  const storageKey = `assets-generator:decision-reuse:${runId}:${nodeId}`;
  const [saved] = useState(() => {
    try {
      const raw = sessionStorage.getItem(storageKey);
      if (!raw) return undefined;
      const value = JSON.parse(raw);
      if (
        value?.body?.node_id !== nodeId ||
        typeof value.body.idempotency_key !== "string" ||
        value.body.confirm !== true
      )
        throw Error("invalid saved confirmation");
      return value as { body: Record<string, unknown> };
    } catch {
      return undefined;
    }
  });
  const [sent, setSent] = useState(Boolean(saved));
  const key = JSON.stringify([runId, nodeId, revision, source]);
  const current = useRef(key);
  current.current = key;
  const active = checked?.key === key ? checked.proposal : undefined;
  const receipt = useRef<{ body: Record<string, unknown> }>(saved);
  return (
    <section aria-label={`复用人工决定 · ${nodeId}`}>
      <p>可提议沿用另一运行的选择；输入和候选必须完全一致，确认前不会执行。</p>
      <select
        aria-label={`决定来源运行 · ${nodeId}`}
        value={source}
        disabled={disabled || sent}
        onChange={(e) => {
          setSource(e.target.value);
          setChecked(undefined);
        }}
      >
        <option value="">选择来源运行…</option>
        {runs
          .filter((r) => r.run_id !== runId)
          .map((r) => (
            <option key={r.run_id} value={r.run_id}>
              {r.run_id}
            </option>
          ))}
      </select>
      <button
        disabled={disabled || !source || sent}
        onClick={() =>
          void mutate(async () => {
            const proposal: Proposal = await request(
              `/api/runs/${encodeURIComponent(runId)}/reuse-decision`,
              {
                node_id: nodeId,
                expected_revision: revision,
                source_run_id: source,
              },
            );
            if (current.current !== key) return;
            if (
              proposal.source_run_id !== source ||
              proposal.node_id !== nodeId ||
              !proposal.source_snapshot?.artifact_id ||
              !proposal.source_decision?.artifact_id
            )
              throw Error("决定来源无法核实");
            setChecked({ key, proposal });
          })
        }
      >
        检查旧选择是否适用
      </button>
      {active && (
        <>
          <p>
            原选择人（自报）：{active.source_reviewer}。来源：
            {active.source_run_id} / {nodeId}
          </p>
          <pre>{JSON.stringify(active.payload, null, 2)}</pre>
          <p>确认会记录你的本次决定并继续此运行；不是质量批准。</p>
          <input
            aria-label={`本次确认人 · ${nodeId}`}
            value={reviewer}
            disabled={disabled || sent}
            onChange={(e) => setReviewer(e.target.value)}
          />
          <button
            disabled={disabled || !reviewer.trim() || sent}
            onClick={() =>
              void mutate(async () => {
                receipt.current = {
                  body: {
                    node_id: nodeId,
                    expected_revision: revision,
                    source_run_id: source,
                    source_snapshot: active.source_snapshot,
                    confirm: true,
                    reviewer: reviewer.trim(),
                    idempotency_key: newRequestId(),
                  },
                };
                sessionStorage.setItem(
                  storageKey,
                  JSON.stringify(receipt.current),
                );
                setSent(true);
                await request(
                  `/api/runs/${encodeURIComponent(runId)}/reuse-decision`,
                  receipt.current.body,
                );
              })
            }
          >
            确认沿用旧选择并继续
          </button>
        </>
      )}
      {sent && (
        <>
          <p>
            确认请求已发送；若响应不明确，可重发同一请求。刷新后请先查看服务端状态。
          </p>
          <button
            disabled={disabled}
            onClick={() =>
              void mutate(async () => {
                await request(
                  `/api/runs/${encodeURIComponent(runId)}/reuse-decision`,
                  receipt.current!.body,
                );
              })
            }
          >
            重试原确认请求
          </button>
        </>
      )}
    </section>
  );
}

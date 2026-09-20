import { useEffect, useRef, useState } from "react";
import type { Pipeline } from "./graph";

type NodeState = {
  status: string;
  recovery_blocked_reason?: string | null;
  attempts?: {
    error_code?: string;
    error_detail?: string;
    status?: string;
    attempt?: number;
  }[];
};
type Run = {
  run_id: string;
  status: string;
  dag?: { revision: number; node_states: Record<string, NodeState> };
};
type Envelope = {
  run: Run;
  busy?: boolean;
  error?: string | null;
  outputs?: { node_id: string; port: string; url: string }[];
};
async function request(path: string, body?: unknown, signal?: AbortSignal) {
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
export function ExecutionPanel({
  pipeline,
  profile,
  executionReason,
}: {
  pipeline: Pipeline;
  profile?: string;
  executionReason?: string;
}) {
  const [imagePath, setImagePath] = useState("");
  const [runs, setRuns] = useState<{ run_id: string; status: string }[]>([]);
  const [selected, setSelected] = useState("");
  const selectedRef = useRef("");
  const [envelope, setEnvelope] = useState<Envelope>();
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  const [review, setReview] = useState<{
    runId: string;
    nodeId: string;
    url: string;
  }>();
  const [snapshot, setSnapshot] = useState<{
    runId: string;
    pipeline: string;
  }>();
  const refreshRuns = async () => setRuns((await request("/api/runs")).runs);
  useEffect(() => {
    void refreshRuns().catch((error) => setMessage(String(error)));
  }, []);
  const choose = (id: string) => {
    selectedRef.current = id;
    setSelected(id);
    setEnvelope(undefined);
    setReview(undefined);
    setMessage("");
  };
  const accept = (value: Envelope) => {
    if (value.run.run_id !== selectedRef.current) return;
    setEnvelope((old) =>
      old?.run.run_id === value.run.run_id &&
      (old.run.dag?.revision || 0) > (value.run.dag?.revision || 0)
        ? old
        : value,
    );
  };
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await request(
          `/api/runs/${encodeURIComponent(selected)}`,
          undefined,
          controller.signal,
        );
        if (!controller.signal.aborted) accept(value);
      } catch (error) {
        if (!controller.signal.aborted)
          setMessage(`读取运行失败：${String(error)}`);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 1500);
      }
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [selected]);
  const mutate = async (action: () => Promise<void>) => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setMessage("");
    try {
      await action();
    } catch (error) {
      setMessage(
        `${String(error)}。未自动重发；若响应丢失，请刷新运行列表核实。`,
      );
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  };
  const run = envelope?.run;
  const executing = pending || !!envelope?.busy;
  return (
    <section className="execution-panel">
      <div className="section-label">创建新运行</div>
      <p>已配置模型：{profile || "本地服务配置"}</p>
      <label>
        服务所在电脑的图片绝对路径
        <input
          aria-label="运行图片路径"
          placeholder="/path/to/image.png"
          value={imagePath}
          onChange={(e) => setImagePath(e.target.value)}
        />
      </label>
      <p>启动时后端重新编译当前草稿并固定计划。修改画布只影响下一次新运行。</p>
      {executionReason && (
        <p role="alert">当前入口不可运行：{executionReason}</p>
      )}
      <button
        className="primary"
        disabled={pending || !imagePath.trim() || !!executionReason}
        onClick={() =>
          void mutate(async () => {
            const submitted = structuredClone(pipeline);
            const value: Envelope = await request("/api/runs", {
              pipeline: submitted,
              image_path: imagePath.trim(),
            });
            choose(value.run.run_id);
            accept(value);
            setSnapshot({
              runId: value.run.run_id,
              pipeline: JSON.stringify(submitted),
            });
            setRuns((old) => [
              { run_id: value.run.run_id, status: value.run.status },
              ...old.filter((item) => item.run_id !== value.run.run_id),
            ]);
            setMessage("运行已创建；请在下方查看真实节点状态。");
          })
        }
      >
        启动新运行
      </button>
      <hr />
      <div className="section-label">运行记录</div>
      <button
        disabled={pending}
        onClick={() =>
          void refreshRuns().catch((error) => setMessage(String(error)))
        }
      >
        刷新运行列表
      </button>
      <select
        aria-label="选择运行"
        value={selected}
        disabled={pending}
        onChange={(e) => choose(e.target.value)}
      >
        <option value="">选择运行…</option>
        {runs.map((item) => (
          <option key={item.run_id} value={item.run_id}>
            {item.run_id} · {item.status}
          </option>
        ))}
      </select>
      {run && (
        <>
          <p className="run-identity">{run.run_id}</p>
          <strong>
            运行状态：{run.status}
            {envelope.busy ? " · 后台处理中" : ""}
          </strong>
          <p>状态仅对应此运行的固定计划，画布仍是可编辑草稿。</p>
          {snapshot?.runId === run.run_id &&
            snapshot.pipeline !== JSON.stringify(pipeline) && (
              <p>草稿已修改，与此运行的计划不同。</p>
            )}
          {envelope.error && <p role="alert">{envelope.error}</p>}
          <button
            disabled={executing}
            onClick={() =>
              void mutate(async () =>
                accept(
                  await request(
                    `/api/runs/${encodeURIComponent(run.run_id)}/resume`,
                    { expected_revision: run.dag!.revision },
                  ),
                ),
              )
            }
          >
            恢复 / 继续此运行
          </button>
          {!!envelope.outputs?.length && (
            <div className="run-node">
              <strong>已发布输出</strong>
              {envelope.outputs.map((output) => (
                <a
                  key={`${output.node_id}:${output.port}`}
                  href={output.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {output.node_id} · {output.port} ↗
                </a>
              ))}
            </div>
          )}
          {Object.entries(run.dag?.node_states || {}).map(([id, state]) => (
            <div className="run-node" key={id}>
              <strong>{id}</strong>
              <span>{state.status}</span>
              {state.recovery_blocked_reason && (
                <p>{state.recovery_blocked_reason}</p>
              )}
              {!!state.attempts?.length && (
                <details>
                  <summary>执行记录 · {state.attempts.length} 次</summary>
                  {state.attempts.map((attempt, index) => (
                    <p key={index}>
                      #{attempt.attempt || index + 1} · {attempt.status}
                      <br />
                      {attempt.error_code} {attempt.error_detail}
                    </p>
                  ))}
                </details>
              )}
              {state.status === "waiting_for_input" && (
                <button
                  disabled={executing}
                  onClick={() =>
                    void mutate(async () => {
                      const value = await request(
                        `/api/runs/${encodeURIComponent(run.run_id)}/review`,
                        { node_id: id },
                      );
                      const url = new URL(value.url, window.location.href);
                      if (
                        url.protocol !== "http:" ||
                        !["localhost", "127.0.0.1"].includes(url.hostname)
                      )
                        throw Error("无效的本地审查地址");
                      setReview({
                        runId: run.run_id,
                        nodeId: id,
                        url: url.href,
                      });
                    })
                  }
                >
                  准备人工审查 · {id}
                </button>
              )}
              {["failed", "interrupted", "recovery_blocked"].includes(
                state.status,
              ) && (
                <button
                  disabled={executing}
                  onClick={() =>
                    void mutate(async () =>
                      accept(
                        await request(
                          `/api/runs/${encodeURIComponent(run.run_id)}/retry`,
                          { node_id: id, expected_revision: run.dag!.revision },
                        ),
                      ),
                    )
                  }
                >
                  显式重试 · {id}
                </button>
              )}
              {review?.runId === run.run_id &&
                review.nodeId === id &&
                state.status === "waiting_for_input" && (
                  <a href={review.url} target="_blank" rel="noreferrer">
                    打开 mask 审查页面 ↗
                  </a>
                )}
            </div>
          ))}
        </>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}

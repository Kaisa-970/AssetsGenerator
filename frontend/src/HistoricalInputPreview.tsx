import { useEffect, useState } from "react";
import { request } from "./executionApi";
import { ImageOutput } from "./ImageOutput";

export type InputOrigin = {
  artifactId: string;
  runId: string;
  nodeId: string;
  port: string;
  snapshot?: string;
};

/** Render a pinned image only after matching its reference to the actual input. */
export function HistoricalInputPreview({
  origin,
  artifactId,
}: {
  origin: InputOrigin;
  artifactId: string;
}) {
  const key = JSON.stringify([origin, artifactId]);
  const [result, setResult] = useState<{
    key: string;
    url?: string;
    error?: string;
  }>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!origin.snapshot) return;
    const controller = new AbortController();
    const isInput = origin.nodeId === `input:${origin.port}`;
    const suffix = `${isInput ? "" : `${encodeURIComponent(origin.nodeId)}/`}${encodeURIComponent(origin.port)}?snapshot=${encodeURIComponent(origin.snapshot)}`;
    const base = `/api/runs/${encodeURIComponent(origin.runId)}`;
    void (async () => {
      try {
        const value = await request(
          `${base}/${isInput ? "snapshot-input-reference" : "snapshot-reference"}/${suffix}`,
          undefined,
          controller.signal,
        );
        if (
          value.source_run_id !== origin.runId ||
          (isInput
            ? value.input_name !== origin.port
            : value.node_id !== origin.nodeId || value.port !== origin.port) ||
          value.source_snapshot?.artifact_id !== origin.snapshot ||
          value.reference?.artifact_id !== artifactId ||
          origin.artifactId !== artifactId ||
          !["rgb_image", "rgba_image", "binary_mask"].includes(value.kind)
        )
          throw Error("历史快照与当前输入不一致");
        if (!controller.signal.aborted)
          setResult({
            key,
            url: `${base}/${isInput ? "snapshot-input-image" : "snapshot-output"}/${suffix}`,
          });
      } catch {
        if (!controller.signal.aborted)
          setResult({
            key,
            error: "无法核实当前输入的历史图片；绑定未改变，请重新核实来源。",
          });
      }
    })();
    return () => controller.abort();
  }, [key, attempt]);
  if (!origin.snapshot)
    return (
      <p>
        此输入没有固定历史快照，无法安全显示历史图片；可重新选用带快照的结果。
      </p>
    );
  if (result?.key !== key) return <p>正在核实当前输入图片…</p>;
  if (result.error)
    return (
      <div>
        <p role="alert">{result.error}</p>
        <button onClick={() => setAttempt((value) => value + 1)}>
          重新读取输入预览
        </button>
      </div>
    );
  return (
    <div className="historical-input-preview">
      <div className="historical-input-source">
        <p>
          固定历史来源：{origin.runId} / {origin.nodeId} / {origin.port}
        </p>
        <details>
          <summary>历史输入来源</summary>
          <p>固定快照：{origin.snapshot}</p>
        </details>
      </div>
      <ImageOutput
        compact
        key={key}
        runId={origin.runId}
        nodeId={origin.nodeId}
        port={origin.port}
        url={result.url!}
        defaultOpen
      />
    </div>
  );
}

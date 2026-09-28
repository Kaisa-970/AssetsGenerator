import { useEffect, useState } from "react";
import { request } from "./executionApi";
import type { Port } from "./graph";
import type { RunOutput } from "./RunOutputs";

export function inputReferenceMismatch(
  source: Record<string, any>,
  runId: string,
  output: RunOutput,
  port: Port,
): string | undefined {
  if (
    source.source_run_id !== runId ||
    source.node_id !== output.node_id ||
    source.port !== output.port
  )
    return "来源身份不匹配";
  if (
    typeof source.reference?.artifact_id !== "string" ||
    !source.reference.artifact_id
  )
    return "没有可核实的 Artifact 引用";
  if (!(port.kinds || [port.kind]).includes(source.kind))
    return "数据类型不匹配";
  if (port.carriers?.length && !port.carriers.includes("artifact_ref"))
    return "输入不接受 Artifact 引用";
  if (port.cardinality && !["one", "zero_or_one"].includes(port.cardinality))
    return "集合输入需要显式集合构造";
  for (const field of ["schema_name", "schema_version"])
    if (port[field] && source[field] !== port[field]) return `${field} 不匹配`;
  for (const field of ["frame_id", "unit"]) {
    if (port[field] && source[field] !== port[field]) return `${field} 不匹配`;
    const required =
      field === "frame_id" ? port.requires_frame : port.requires_unit;
    if (
      required &&
      (typeof source[field] !== "string" || !source[field].trim())
    )
      return `来源缺少可核实的 ${field}`;
  }
}

export function InputHistorySelector({
  name,
  port,
  runId,
  outputs,
  disabled,
  onSelect,
}: {
  name: string;
  port: Port;
  runId?: string;
  outputs: RunOutput[];
  disabled: boolean;
  onSelect: (output: RunOutput, name: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [checked, setChecked] = useState<{
    key: string;
    reasons: (string | undefined)[];
  }>();
  const [retry, setRetry] = useState(0);
  const candidates = outputs.filter((output) =>
    (port.kinds || [port.kind]).includes(output.kind),
  );
  const key = JSON.stringify([runId, candidates, port, retry]);
  useEffect(() => {
    if (!open || !runId) return;
    const controller = new AbortController();
    let active = true;
    void (async () => {
      const reasons: (string | undefined)[] = [];
      // Bound the read load; no recovery, mutation, or inference is triggered.
      for (const output of candidates) {
        if (!active) return;
        try {
          const source = await request(
            `/api/runs/${encodeURIComponent(runId)}/references/${encodeURIComponent(output.node_id)}/${encodeURIComponent(output.port)}`,
            undefined,
            controller.signal,
          );
          reasons.push(inputReferenceMismatch(source, runId, output, port));
        } catch {
          reasons.push("来源证据暂时无法核实");
        }
      }
      if (active) setChecked({ key, reasons });
    })();
    return () => {
      active = false;
      controller.abort();
    };
  }, [open, key]);
  return (
    <details
      className="input-history-selector"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>从历史结果选择 · {name}</summary>
      <p>在顶部选择来源运行；查看历史不会自动绑定输入。</p>
      <p>来源运行：{runId || "尚未选择"}</p>
      {!candidates.length ? (
        <p>当前运行没有同类型的可用输出。</p>
      ) : checked?.key !== key ? (
        <p>正在核实候选来源与输入契约…</p>
      ) : (
        candidates.map((output, index) => (
          <div key={`${output.node_id}:${output.port}`}>
            {checked.reasons[index] ? (
              <p>
                {output.node_id}.{output.port}：{checked.reasons[index]}
              </p>
            ) : (
              <button
                disabled={disabled}
                onClick={() => onSelect(output, name)}
              >
                选用 {output.node_id}.{output.port} → {name}
              </button>
            )}
          </div>
        ))
      )}
      {runId && (
        <button
          disabled={disabled || (checked?.key !== key && candidates.length > 0)}
          onClick={() => setRetry((value) => value + 1)}
        >
          重新核实候选
        </button>
      )}
      <small>选用时再次核实来源；多输入之间的语义关系仍由后端启动检查。</small>
    </details>
  );
}

import { useRef, useState } from "react";
import type { Pipeline } from "./graph";

type Intent = {
  pipeline: Pipeline;
  reuse_source?: { artifact_id: string };
  image_path?: string;
  image_ref?: Record<string, unknown>;
  observations_ref?: Record<string, unknown>;
  input_refs?: Record<string, Record<string, unknown>>;
};
type Prepared = Intent & { preflight_digest: string };
type Report = {
  digest: string;
  execution_ready: boolean;
  nodes: Record<string, { status: string; detail: string; reason: string }>;
  source_evidence_policy: string;
  error?: { detail: string };
  entry_error?: string;
};
const labels: Record<string, string> = {
  reuse: "将复用",
  execute: "将执行",
  blocked: "受阻",
  await_upstream: "待上游结果确认",
};

async function post(path: string, body: unknown) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    redirect: "error",
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || `HTTP ${response.status}`);
  return result;
}

export function ExecutionPreflight({
  intent,
  disabled,
  label,
  onConfirm,
}: {
  intent: Intent;
  disabled: boolean;
  label: string;
  onConfirm: (prepared: Prepared) => Promise<void>;
}) {
  const key = JSON.stringify(intent);
  const current = useRef(key);
  current.current = key;
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [error, setError] = useState("");
  const [checked, setChecked] = useState<{
    key: string;
    prepared: Prepared;
    report: Report;
  }>();
  const active = checked?.key === key ? checked : undefined;
  const check = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setChecked(undefined);
    try {
      // Import is explicit and separate from the read-only analysis request.
      const { reuse_source, ...inputIntent } = intent;
      const inputs = await post("/api/prepare-inputs", inputIntent);
      if (current.current !== key) return;
      const prepared = {
        pipeline: intent.pipeline,
        input_refs: inputs.input_refs,
        ...(reuse_source ? { reuse_source } : {}),
      };
      const report: Report = await post("/api/preflight", prepared);
      if (current.current !== key) return;
      if (
        typeof report.digest !== "string" ||
        !report.nodes ||
        typeof report.execution_ready !== "boolean"
      )
        throw Error("预检响应无法核实");
      const checkedResult = {
        key,
        prepared: { ...prepared, preflight_digest: report.digest },
        report,
      };
      const requiresConfirmation =
        Boolean(intent.reuse_source) || label !== "启动新运行";
      if (report.execution_ready && !requiresConfirmation) {
        // Ordinary runs keep the preflight as an internal preparation step.
        // The server still receives the digest and performs its own second check.
        await onConfirm(checkedResult.prepared);
        return;
      }
      setChecked(checkedResult);
    } catch (cause) {
      if (current.current === key) setError(String(cause));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  return (
    <section aria-label="执行前预检">
      <button
        className="primary"
        aria-label={`${label} · 检查执行范围`}
        disabled={disabled || busy}
        onClick={() => void check()}
      >
        {busy ? "正在检查当前配置…" : label}
      </button>
      <p>启动时会自动检查输入和执行范围。</p>
      {checked && !active && <p>输入、配置或来源已变化，请重新检查。</p>}
      {error && <p role="alert">{error}</p>}
      {active && (
        <>
          {(active.report.entry_error || active.report.error) && (
            <p role="alert">
              {active.report.entry_error || active.report.error?.detail}
            </p>
          )}
          {Object.entries(active.report.nodes).map(([id, node]) => (
            <div key={id}>
              {id} · {labels[node.status] || "无法核实"}
              <details>
                <summary>原因</summary>
                {node.detail} ({node.reason})
              </details>
            </div>
          ))}
          <p>检查范围：Core 契约与复用证据；服务可达性及运行资源尚未检查。</p>
          {intent.reuse_source && (
            <p>当前复用要求来源快照的完整证据均可核实。</p>
          )}
          <button
            disabled={disabled || busy || !active.report.execution_ready}
            onClick={() => {
              const prepared = active.prepared;
              setChecked(undefined);
              void onConfirm(prepared);
            }}
          >
            确认执行上述范围
          </button>
          <p>启动时再次核验；执行或复用条件变化会停止创建，要求重新检查。</p>
        </>
      )}
    </section>
  );
}

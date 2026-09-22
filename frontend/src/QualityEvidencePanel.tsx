import { useEffect, useState } from "react";
type Output = { node_id: string; port: string; kind?: string; url: string };
type Status = "pass" | "warn" | "fail" | "skipped";
type Check = {
  id: string;
  applicable: boolean;
  status: Status;
  value: string | number | null;
  profile: string;
  reason: string | null;
};
type Evidence =
  | {
      kind: "quality_report";
      overall: Exclude<Status, "skipped">;
      profile: string;
      checks: Check[];
    }
  | {
      kind: "asset_definition";
      scale: string | null;
      unit: string | null;
      forward: string | null;
      sources: { component: string; source: string }[];
    };
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw Error("记录格式无效");
  return value as Record<string, unknown>;
}
function text(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) throw Error("记录字段无效");
  return value;
}
function choice<T extends string>(value: unknown, choices: readonly T[]): T {
  if (typeof value !== "string" || !choices.includes(value as T))
    throw Error("记录状态无效");
  return value as T;
}
/** Check displayed evidence fields without deriving quality from execution. */
export function parseQualityEvidence(kind: string, value: unknown): Evidence {
  const raw = record(value);
  if (kind === "quality_report") {
    const overall = choice(raw.overall_status, [
      "pass",
      "warn",
      "fail",
    ] as const);
    const profile = text(raw.profile);
    if (!Array.isArray(raw.checks)) throw Error("检查列表无效");
    const checks = raw.checks.map((item): Check => {
      const c = record(item);
      const status = choice(c.status, [
        "pass",
        "warn",
        "fail",
        "skipped",
      ] as const);
      if (typeof c.applicable !== "boolean") throw Error("检查适用性无效");
      if (
        c.value !== null &&
        typeof c.value !== "string" &&
        !(typeof c.value === "number" && Number.isFinite(c.value))
      )
        throw Error("检查值无效");
      if (
        c.reason !== undefined &&
        c.reason !== null &&
        typeof c.reason !== "string"
      )
        throw Error("检查原因无效");
      return {
        id: text(c.check_id),
        applicable: c.applicable,
        status,
        value: c.value as Check["value"],
        profile: text(c.threshold_profile),
        reason: (c.reason as string | null) ?? null,
      };
    });
    if (new Set(checks.map((c) => c.id)).size !== checks.length)
      throw Error("检查 ID 重复");
    return { kind, overall, profile, checks };
  }
  if (kind !== "asset_definition") throw Error("不支持的证据类型");
  text(raw.asset_id);
  text(raw.asset_version);
  const spatial = raw.spatial === undefined ? {} : record(raw.spatial);
  const scale =
    spatial.scale_status === undefined
      ? null
      : choice(spatial.scale_status, ["metric", "relative", "unknown"]);
  const forward =
    spatial.forward_status === undefined
      ? null
      : choice(spatial.forward_status, ["declared", "estimated", "unknown"]);
  const unit = spatial.unit === undefined ? null : text(spatial.unit);
  if (
    raw.component_provenance !== undefined &&
    !Array.isArray(raw.component_provenance)
  )
    throw Error("组件来源列表无效");
  const sources = ((raw.component_provenance ?? []) as unknown[]).map(
    (item) => {
      const c = record(item);
      return {
        component: text(c.component_id),
        source: choice(c.source, [
          "observed",
          "reconstructed",
          "generated",
          "mixed",
        ]),
      };
    },
  );
  return { kind, scale, unit, forward, sources };
}
function evidenceKind(
  output: Output,
): "quality_report" | "asset_definition" | undefined {
  if (output.kind !== undefined)
    return output.kind === "quality_report" ||
      output.kind === "asset_definition"
      ? output.kind
      : undefined;
  return output.port === "qa"
    ? "quality_report"
    : output.port === "asset"
      ? "asset_definition"
      : undefined;
}
export async function readQualityEvidence(
  runId: string,
  output: Output,
  signal: AbortSignal,
): Promise<Evidence> {
  const expected = `/api/runs/${encodeURIComponent(runId)}/outputs/${encodeURIComponent(output.node_id)}/${encodeURIComponent(output.port)}`;
  if (output.url !== expected) throw Error("输出地址与来源不符");
  const kind = evidenceKind(output);
  if (!kind) throw Error("不支持的证据类型");
  const response = await fetch(output.url, {
    signal,
    cache: "no-store",
    redirect: "error",
  });
  if (!response.ok) throw Error(`HTTP ${response.status}`);
  return parseQualityEvidence(kind, await response.json());
}
const statusLabel: Record<Status, string> = {
  pass: "pass · 通过",
  warn: "warn · 警告",
  fail: "fail · 不通过",
  skipped: "skipped · 未执行该项检查",
};
export function QualityEvidenceBody({ evidence }: { evidence: Evidence }) {
  if (evidence.kind === "quality_report")
    return (
      <>
        <p>QA 报告汇总：{statusLabel[evidence.overall]}</p>
        <p>检查配置：{evidence.profile}</p>
        {evidence.checks.length === 0 ? (
          <p>逐项检查：未评估（无记录）</p>
        ) : (
          <ul>
            {evidence.checks.map((c) => (
              <li key={c.id}>
                <strong>
                  {c.id}：{statusLabel[c.status]}
                </strong>
                <p>
                  适用性：{c.applicable ? "适用" : "不适用"}；值：
                  {c.value ?? "未记录"}；阈值配置：{c.profile}
                </p>
                <p>原因：{c.reason || "未记录"}</p>
              </li>
            ))}
          </ul>
        )}
      </>
    );
  const scale =
    evidence.scale === "relative"
      ? "相对尺度，未标定为米制"
      : evidence.scale === "metric"
        ? "metric · 已记录米制尺度"
        : "未评估";
  const forward =
    evidence.forward === "declared"
      ? "declared · 已声明"
      : evidence.forward === "estimated"
        ? "estimated · 估计方向"
        : "未评估";
  return (
    <>
      <p>尺度：{scale}</p>
      <p>单位：{evidence.unit ?? "未评估"}</p>
      <p>前向：{forward}</p>
      <p>组件来源：</p>
      {evidence.sources.length === 0 ? (
        <p>未评估（无记录）</p>
      ) : (
        <ul>
          {evidence.sources.map((s, i) => (
            <li key={i}>
              {s.component}：{s.source}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
function EvidenceOutput({ runId, output }: { runId: string; output: Output }) {
  const [result, setResult] = useState<Evidence | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void readQualityEvidence(runId, output, controller.signal).then(
      (value) => {
        if (!controller.signal.aborted) setResult(value);
      },
      (failure: unknown) => {
        if (!controller.signal.aborted)
          setError(failure instanceof Error ? failure.message : "读取失败");
      },
    );
    return () => controller.abort();
  }, [runId, output.node_id, output.port, output.kind, output.url]);
  return (
    <section
      aria-label={`${evidenceKind(output) === "quality_report" ? "QA" : "资产边界"} · ${output.node_id} · ${output.port}`}
    >
      <p className="run-identity">
        来源：{runId} / {output.node_id} / {output.port}
      </p>
      {error ? (
        <p role="alert">无法核实：{error}</p>
      ) : result ? (
        <QualityEvidenceBody evidence={result} />
      ) : (
        <p role="status">正在读取证据…</p>
      )}
    </section>
  );
}
/** Keyed by immutable origin: a run switch immediately unmounts all old results. */
export function QualityEvidencePanel({
  runId,
  outputs,
}: {
  runId: string;
  outputs: Output[];
}) {
  const relevant = outputs.filter((output) => evidenceKind(output));
  return (
    <section aria-label="QA 与资产边界">
      <h3>QA 与资产边界</h3>
      <p>来源运行：{runId}。执行完成不代表质量合格。</p>
      {!relevant.some((o) => evidenceKind(o) === "quality_report") && (
        <p>QA：未评估（无报告记录）</p>
      )}
      {!relevant.some((o) => evidenceKind(o) === "asset_definition") && (
        <p>资产边界：未评估（无资产记录）</p>
      )}
      {relevant.map((output) => (
        <EvidenceOutput
          key={JSON.stringify([
            runId,
            output.node_id,
            output.port,
            output.kind,
            output.url,
          ])}
          runId={runId}
          output={output}
        />
      ))}
    </section>
  );
}

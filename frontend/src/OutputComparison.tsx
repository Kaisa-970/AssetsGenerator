import { lazy, Suspense, useState } from "react";
import { ImageOutput } from "./ImageOutput";
import { portKinds, type Pipeline, type Port } from "./graph";
import "./OutputComparison.css";

const GlbPreview = lazy(() =>
  import("./GlbPreview").then((module) => ({ default: module.GlbPreview })),
);

export type ComparisonSide = "A" | "B";
export type ComparisonSlot = {
  runId: string;
  nodeId: string;
  port: string;
  kind: string;
  snapshot: string;
  reference: { artifact_id: string };
  schema_name?: string;
  schema_version?: string;
};

export function comparisonOutputUrl(slot: ComparisonSlot) {
  return `/api/runs/${encodeURIComponent(slot.runId)}/snapshot-output/${encodeURIComponent(slot.nodeId)}/${encodeURIComponent(slot.port)}?snapshot=${encodeURIComponent(slot.snapshot)}`;
}

export type ComparisonSelection = {
  artifactId: string;
  runId?: string;
  nodeId?: string;
  port?: string;
  snapshot?: string;
};

export function comparisonSelectionMatches(
  slot: ComparisonSlot,
  value?: ComparisonSelection,
) {
  return (
    !!value &&
    value.artifactId === slot.reference.artifact_id &&
    value.runId === slot.runId &&
    value.nodeId === slot.nodeId &&
    value.port === slot.port &&
    value.snapshot === slot.snapshot
  );
}

export function comparisonInputCompatible(slot: ComparisonSlot, input: Port) {
  return (
    portKinds(input).includes(slot.kind) &&
    (!input.schema_name || input.schema_name === slot.schema_name) &&
    (!input.schema_version || input.schema_version === slot.schema_version) &&
    (!input.carriers || input.carriers.includes("artifact_ref")) &&
    ["one", "zero_or_one"].includes(input.cardinality || "one")
  );
}

export function OutputComparison({
  slots,
  inputs,
  disabled,
  selectedInputs,
  onUse,
  onClear,
}: {
  slots: Partial<Record<ComparisonSide, ComparisonSlot>>;
  inputs: Pipeline["inputs"];
  disabled: boolean;
  selectedInputs: Record<string, ComparisonSelection>;
  onUse: (slot: ComparisonSlot, inputName: string) => void;
  onClear: (side: ComparisonSide) => void;
}) {
  const [model, setModel] = useState<ComparisonSlot>();
  return (
    <section className="output-comparison" aria-label="比较两份结果">
      <h3>比较两份结果</h3>
      <p>
        预览不会选择下游输入。请明确点击“用作下游输入”；系统不判定哪份更好。
      </p>
      <div className="output-comparison-columns">
        {(["A", "B"] as const).map((side) => {
          const slot = slots[side];
          if (!slot)
            return (
              <article key={side} aria-label={`比较 ${side}`}>
                <h4>{side}</h4>
                <p>尚未固定结果。</p>
              </article>
            );
          const url = comparisonOutputUrl(slot);
          const identity = JSON.stringify([
            slot.runId,
            slot.nodeId,
            slot.port,
            slot.snapshot,
            slot.reference.artifact_id,
          ]);
          const matches = Object.entries(inputs).filter(([, input]) =>
            comparisonInputCompatible(slot, input),
          );
          return (
            <article key={`${side}:${identity}`} aria-label={`比较 ${side}`}>
              <h4>{side} · 历史结果</h4>
              <p>这份结果来自已完成的运行，可单独预览。</p>
              <details>
                <summary>查看来源详情</summary>
                <p>
                  {slot.runId} / {slot.nodeId} / {slot.port}
                </p>
                <p className="comparison-artifact">
                  Artifact：{slot.reference.artifact_id}
                </p>
                <p className="comparison-artifact">固定快照：{slot.snapshot}</p>
              </details>
              {["rgb_image", "rgba_image", "binary_mask"].includes(
                slot.kind,
              ) ? (
                <ImageOutput
                  runId={slot.runId}
                  nodeId={slot.nodeId}
                  port={slot.port}
                  url={url}
                  defaultOpen
                />
              ) : slot.kind === "gltf_asset" ? (
                <button onClick={() => setModel(structuredClone(slot))}>
                  预览模型 · {side}
                </button>
              ) : (
                <p>此类型暂无可视预览：{slot.kind}</p>
              )}
              {matches.map(([name]) => (
                <div key={name}>
                  {comparisonSelectionMatches(slot, selectedInputs[name]) && (
                    <p>已选作下游输入 {name}；绑定此运行、节点、端口及快照。</p>
                  )}
                  <button disabled={disabled} onClick={() => onUse(slot, name)}>
                    用作下游输入 {name} · {side}
                  </button>
                </div>
              ))}
              {!matches.length && (
                <p>当前草稿没有类型、schema 与载体匹配的标量输入。</p>
              )}
              <button disabled={disabled} onClick={() => onClear(side)}>
                移除比较项 · {side}
              </button>
            </article>
          );
        })}
      </div>
      {model && (
        <Suspense fallback={<p role="status">正在加载模型预览…</p>}>
          <GlbPreview
            runId={model.runId}
            nodeId={model.nodeId}
            url={comparisonOutputUrl(model)}
            onClose={() => setModel(undefined)}
          />
        </Suspense>
      )}
    </section>
  );
}

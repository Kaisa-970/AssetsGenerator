import type { ComponentProps } from "react";
import { ContinueExtraction } from "./ContinueExtraction";
import { ImageOutput } from "./ImageOutput";
import type { Envelope } from "./executionApi";
import type { Pipeline } from "./graph";

export type RunOutput = NonNullable<Envelope["outputs"]>[number];

type Props = {
  runId: string;
  status: string;
  outputs: RunOutput[];
  snapshot?: string;
  inputs: Pipeline["inputs"];
  disabled: {
    continuation: boolean;
    comparison: boolean;
    inputBinding: boolean;
  };
  onPreview: (preview: { runId: string; nodeId: string; url: string }) => void;
  onContinueExtraction: ComponentProps<typeof ContinueExtraction>["onConfirm"];
  onCompare: (output: RunOutput, side: "A" | "B") => void;
  onUseAsInput: (output: RunOutput, inputName: string) => void;
};

// Render output affordances; the owner verifies sources and changes run/input state.
export function RunOutputs({
  runId,
  status,
  outputs,
  snapshot,
  inputs,
  disabled,
  onPreview,
  onContinueExtraction,
  onCompare,
  onUseAsInput,
}: Props) {
  return (
    <>
      {status === "succeeded" && !outputs.length && (
        <p>
          运行已完成，暂无可预览图片；分割可能未找到符合提示词和阈值的候选。
        </p>
      )}
      {!!outputs.length && (
        <div className="run-node">
          <strong>可查看的节点输出</strong>
          {outputs
            .filter(
              (output) =>
                output.kind === "gltf_asset" ||
                output.kind === "triangle_mesh" ||
                (!output.kind && output.port === "glb"),
            )
            .map((output) => (
              <button
                key={`preview:${output.node_id}:${output.port}`}
                onClick={() =>
                  onPreview({
                    runId,
                    nodeId: output.node_id,
                    url: output.url,
                  })
                }
              >
                预览模型 · {output.node_id} · {output.port}
              </button>
            ))}
          {snapshot &&
            outputs
              .filter(
                (output) =>
                  output.kind === "binary_mask" && output.port === "mask",
              )
              .map((output) => (
                <ContinueExtraction
                  key={`${runId}:${output.node_id}:${snapshot}`}
                  runId={runId}
                  nodeId={output.node_id}
                  snapshot={snapshot}
                  disabled={disabled.continuation}
                  onConfirm={onContinueExtraction}
                />
              ))}
          {snapshot &&
            outputs
              .filter((output) =>
                [
                  "rgb_image",
                  "rgba_image",
                  "binary_mask",
                  "gltf_asset",
                  "triangle_mesh",
                ].includes(output.kind || ""),
              )
              .flatMap((output) =>
                (["A", "B"] as const).map((side) => (
                  <button
                    key={`compare:${side}:${output.node_id}:${output.port}`}
                    disabled={disabled.comparison}
                    onClick={() => {
                      onCompare(output, side);
                    }}
                  >
                    加入比较 {side} · {output.node_id} · {output.port}
                  </button>
                )),
              )}
          {outputs
            .filter((output) => output.kind === "asset_release")
            .map((output) => (
              <a
                key={`archive:${output.node_id}:${output.port}`}
                href={`/api/runs/${encodeURIComponent(runId)}/archives/${encodeURIComponent(output.node_id)}/${encodeURIComponent(output.port)}`}
                download={`${runId}-${output.node_id}.zip`}
              >
                下载发布包 · {output.node_id}
              </a>
            ))}
          {outputs
            .filter((output) =>
              ["rgb_image", "rgba_image", "binary_mask"].includes(
                output.kind || "",
              ),
            )
            .map((output) => (
              <ImageOutput
                key={`${runId}:${output.node_id}:${output.port}:${output.url}`}
                runId={runId}
                nodeId={output.node_id}
                port={output.port}
                url={output.url}
              />
            ))}
          {outputs.flatMap((output) =>
            Object.entries(inputs)
              .filter(([, port]) =>
                (port.kinds || [port.kind]).includes(output.kind),
              )
              .map(([name]) => (
                <button
                  key={`reuse:${output.node_id}:${output.port}:${name}`}
                  disabled={disabled.inputBinding}
                  onClick={() => {
                    onUseAsInput(output, name);
                  }}
                >
                  用作输入 {name} · {output.node_id} · {output.port}
                </button>
              )),
          )}
          {outputs.map((output) => (
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
    </>
  );
}

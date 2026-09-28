import {
  createElement,
  isValidElement,
  type ReactNode,
  type ReactElement,
} from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ContinueExtraction } from "./ContinueExtraction";
import { ImageOutput } from "./ImageOutput";
import { RunOutputs, type RunOutput } from "./RunOutputs";

const outputs: RunOutput[] = [
  { node_id: "model", port: "mesh", kind: "triangle_mesh", url: "/mesh" },
  {
    node_id: "model",
    port: "collision",
    kind: "triangle_mesh",
    url: "/collision",
  },
  { node_id: "segment", port: "mask", kind: "binary_mask", url: "/mask" },
  {
    node_id: "segment",
    port: "candidate",
    kind: "binary_mask",
    url: "/candidate",
  },
  {
    node_id: "export/file",
    port: "release",
    kind: "asset_release",
    url: "/release",
  },
  { node_id: "legacy", port: "glb", url: "/legacy" },
];
const props = () => ({
  runId: "run/source",
  status: "succeeded",
  outputs,
  snapshot: "sha256:source",
  inputs: { mask: { kind: "binary_mask" }, image: { kind: "rgb_image" } },
  disabled: { continuation: false, comparison: false, inputBinding: false },
  onPreview: vi.fn(),
  onCompare: vi.fn(),
  onUseAsInput: vi.fn(),
  onContinueExtraction: vi.fn(async () => {}),
});
function elements(node: ReactNode): ReactElement<Record<string, any>>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<Record<string, any>>(node)) return [];
  return [node, ...elements(node.props.children)];
}
function text(node: ReactNode): string {
  if (Array.isArray(node)) return node.map(text).join("");
  if (isValidElement<{ children?: ReactNode }>(node))
    return text(node.props.children);
  return node == null ? "" : String(node);
}

describe("run output presentation", () => {
  it("keeps same-node model ports distinct and previews their exact source", () => {
    const options = props();
    const buttons = elements(RunOutputs(options)).filter(
      (e) => e.type === "button" && text(e).startsWith("预览模型"),
    );
    expect(buttons.map(text)).toEqual([
      "预览模型 · model · mesh",
      "预览模型 · model · collision",
      "预览模型 · legacy · glb",
    ]);
    expect(new Set(buttons.map((b) => b.key)).size).toBe(3);
    buttons[1].props.onClick();
    expect(options.onPreview).toHaveBeenCalledWith({
      runId: "run/source",
      nodeId: "model",
      url: "/collision",
    });
    expect(options.onUseAsInput).not.toHaveBeenCalled();
  });
  it("requires a snapshot for comparison and continues only the union mask", () => {
    const options = props();
    const tree = elements(RunOutputs(options));
    const continuation = tree.filter((e) => e.type === ContinueExtraction);
    expect(continuation).toHaveLength(1);
    expect(continuation[0].props).toMatchObject({
      runId: options.runId,
      nodeId: "segment",
      snapshot: options.snapshot,
      onConfirm: options.onContinueExtraction,
    });
    const comparison = tree.find(
      (e) => text(e) === "加入比较 B · model · collision",
    )!;
    comparison.props.onClick();
    expect(options.onCompare).toHaveBeenCalledWith(outputs[1], "B");
    const withoutSnapshot = elements(
      RunOutputs({ ...options, snapshot: undefined }),
    );
    expect(withoutSnapshot.some((e) => e.type === ContinueExtraction)).toBe(
      false,
    );
    expect(
      withoutSnapshot.some(
        (e) => e.type === "button" && text(e).startsWith("加入比较"),
      ),
    ).toBe(false);
  });
  it("matches input kinds and forwards explicit binding independently of previews", () => {
    const options = props();
    const tree = elements(RunOutputs(options));
    const bindings = tree.filter(
      (e) => e.type === "button" && text(e).startsWith("用作输入"),
    );
    expect(bindings.map(text)).toEqual([
      "用作输入 mask · segment · mask",
      "用作输入 mask · segment · candidate",
    ]);
    bindings[1].props.onClick();
    expect(options.onUseAsInput).toHaveBeenCalledWith(outputs[3], "mask");
    expect(options.onPreview).not.toHaveBeenCalled();
    expect(tree.filter((e) => e.type === ImageOutput)).toHaveLength(2);
  });
  it("keeps distinct stable identities for comparison and input actions", () => {
    const actions = elements(RunOutputs(props())).filter(
      (e) => e.type === "button" && /^(加入比较|用作输入)/.test(text(e)),
    );
    expect(actions.length).toBeGreaterThan(2);
    expect(actions.every((e) => typeof e.key === "string")).toBe(true);
    expect(new Set(actions.map((e) => e.key)).size).toBe(actions.length);
  });
  it("keeps continuation, comparison and input binding gates independent", () => {
    const tree = elements(
      RunOutputs({
        ...props(),
        disabled: { continuation: true, comparison: false, inputBinding: true },
      }),
    );
    expect(
      tree.find((e) => e.type === ContinueExtraction)!.props.disabled,
    ).toBe(true);
    for (const button of tree.filter((e) => e.type === "button")) {
      if (text(button).startsWith("加入比较"))
        expect(button.props.disabled).toBe(false);
      if (text(button).startsWith("用作输入"))
        expect(button.props.disabled).toBe(true);
      if (text(button).startsWith("预览模型"))
        expect(button.props.disabled).toBeUndefined();
    }
  });
  it("retains encoded archive downloads, raw links and completed empty-output feedback", () => {
    const html = renderToStaticMarkup(createElement(RunOutputs, props()));
    expect(html).toContain(
      "/api/runs/run%2Fsource/archives/export%2Ffile/release",
    );
    expect(html).toContain('href="/collision"');
    const renderEmpty = (status: string) =>
      renderToStaticMarkup(
        createElement(RunOutputs, { ...props(), outputs: [], status }),
      );
    expect(renderEmpty("succeeded")).toContain("运行已完成，暂无可预览图片");
    expect(renderEmpty("running")).toBe("");
  });
});

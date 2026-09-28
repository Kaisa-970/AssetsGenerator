import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
vi.mock("@xyflow/react", () => ({
  Handle: (props: any) =>
    createElement("i", {
      "data-handle": props.id,
      "data-direction": props.type,
    }),
  Position: { Left: "left", Right: "right" },
  useUpdateNodeInternals: () => vi.fn(),
}));
import {
  BlueprintNode,
  blueprintKindLabel,
  blueprintParameterFields,
  blueprintPortTooltip,
  protectBlueprintControlKeyboard,
  type BlueprintNodeData,
} from "./BlueprintNode";
const base: BlueprintNodeData = {
  nodeId: "segment",
  label: "文字分割",
  operator: "segment@1",
  inputs: { image: { kind: "rgb_image" } },
  outputs: { mask: { kind: "binary_mask" } },
};
const render = (data: BlueprintNodeData) =>
  renderToStaticMarkup(
    createElement(BlueprintNode, { data, selected: true } as any),
  );
describe("blueprint node", () => {
  it("keeps real port identity while showing readable labels and exact contract tooltip", () => {
    const html = render(base);
    expect(html).toContain('data-handle="image"');
    expect(html).toContain('data-handle="mask"');
    expect(html).toContain("遮罩");
    expect(html).toContain("rgb_image");
    expect(blueprintKindLabel({ kind: "future_kind" })).toBe("future_kind");
    expect(
      blueprintPortTooltip("mesh", {
        kind: "triangle_mesh",
        schema_name: "Mesh",
        schema_version: "1",
        frame_id: "canonical",
        unit: "meter",
      }),
    ).toContain("frame：canonical · unit：meter");
  });
  it("uses schema parameter form without inventing parameter handles", () => {
    const html = render({
      ...base,
      parameterSchema: {
        name: "segment",
        version: "1",
        operators: ["segment@1"],
        parameter_schema: { properties: { prompt: { type: "string" } } },
      },
      parameters: { prompt: "cup" },
      drafts: {},
      errors: {},
      setDraft: vi.fn(),
      setErrors: vi.fn(),
      onParametersChange: vi.fn(),
      backends: [{ value: "backend-identity", label: "SAM 文字分割" }],
      backend: "backend-identity",
      onBackendChange: vi.fn(),
      adapterId: "private-adapter@1",
    });
    expect(html).toContain("SAM 文字分割");
    expect(html).toContain('aria-label="节点参数 prompt"');
    expect(html).not.toContain('data-handle="prompt"');
    expect(html).toContain('class="blueprint-technical"');
    expect(html).toContain("private-adapter@1");
    expect(html).toContain("nodrag nowheel nopan");
  });
  it("shows the readable operation and connection progress in the node", () => {
    const html = render({
      ...base,
      operator: "text_segmentation@2",
      operatorLabel: "文字分割",
      inputBindings: { image: "pipeline.inputs.image" },
    });
    expect(html).toContain("文字分割");
    expect(html).toContain("· segment");
    expect(html).toContain("输入 1/1 已连接");
  });
  it("renders input bridge slot and marks historical execution separately", () => {
    const html = render({
      ...base,
      nodeId: "input:image",
      operator: "Pipeline input",
      inputSlot: createElement("button", null, "上传图片"),
      status: "succeeded",
    });
    expect(html).toContain("上传图片");
    expect(html).toContain("历史 · 完成");
    expect(html).toContain("blueprint-node-input");
  });
  it("marks a historical node stale when its current graph changed", () => {
    const html = render({
      ...base,
      status: "succeeded",
      freshness: "配置变化 · 需要更新",
    });
    expect(html).toContain("需更新");
    expect(html).toContain("需要重新执行");
  });
  it("shows a concise failure summary without replacing the run details", () => {
    const html = render({
      ...base,
      status: "failed",
      failureSummary: "显存不足 · 调低模型分辨率或释放 GPU 后重试",
    });
    expect(html).toContain("显存不足");
  });
  it("shows one actionable configuration summary inside the node", () => {
    const html = render({
      ...base,
      inputBindings: {},
      parameterSchema: {
        name: "segment",
        version: "1",
        operators: ["segment@1"],
        parameter_schema: { required: ["prompt"], properties: { prompt: { type: "string" } } },
      },
      parameters: {},
      adapters: [],
      backends: [{ value: "sam", label: "SAM" }],
      onBackendChange: vi.fn(),
      setDraft: vi.fn(),
      setErrors: vi.fn(),
      onParametersChange: vi.fn(),
    });
    expect(html).toContain('aria-label="配置状态 segment"');
    expect(html).toContain("缺少输入：图片");
    expect(html).not.toContain("请选择模型实现");
    expect(html).toContain("缺少必填参数：prompt");
    expect(html).not.toContain("请选择模型实现");
  });
  it("stops graph shortcut propagation without cancelling native editing", () => {
    const stopPropagation = vi.fn();
    const preventDefault = vi.fn();
    protectBlueprintControlKeyboard({ stopPropagation, preventDefault } as any);
    expect(stopPropagation).toHaveBeenCalledOnce();
    expect(preventDefault).not.toHaveBeenCalled();
  });
});

it("keeps required fields visible while collapsing optional fields", () => {
  const adapter = {
    name: "test",
    version: "1",
    operators: ["segment@1"],
    parameter_schema: {
      required: ["fourth"],
      properties: {
        fixed: { type: "string", enum: ["deployment"] },
        first: { type: "number" },
        second: { type: "string" },
        third: { type: "boolean" },
        fourth: { type: "string" },
      },
    },
  };
  expect(blueprintParameterFields(adapter)).toEqual({
    primary: ["first", "second", "third"],
    all: ["fixed", "first", "second", "third", "fourth"],
  });
  const html = render({
    ...base,
    parameterSchema: adapter,
    setDraft: vi.fn(),
    setErrors: vi.fn(),
    onParametersChange: vi.fn(),
  });
  expect(html).toContain('aria-label="节点参数 first"');
  expect(html).toContain('aria-label="节点参数 fourth"');
  expect(html).not.toContain('aria-label="节点参数 fixed"');
  expect(html).toContain("缺少必填参数：fourth");
  expect(html).toContain("更多参数");
  expect(html).toContain('aria-expanded="false"');
});

it("reports supplied values separately from unapplied edits and validity", () => {
  const html = render({
    ...base,
    parameterSchema: {
      name: "test",
      version: "1",
      operators: ["segment@1"],
      defaults: { count: 0, enabled: false, text: "" },
      parameter_schema: {
        required: ["count", "enabled", "text"],
        properties: {
          count: { type: "integer" },
          enabled: { type: "boolean" },
          text: { type: "string" },
        },
      },
    },
    drafts: { count: "invalid" },
    setDraft: vi.fn(),
    setErrors: vi.fn(),
    onParametersChange: vi.fn(),
  });
  expect(html).toContain("3/3 已提供");
  expect(html).toContain("有未应用编辑");
  expect(html).not.toContain("缺少必填");
});

it("optional ports stay connectable without being reported as missing required inputs", () => {
  const html = render({
    ...base,
    inputs: {
      image: { kind: "rgb_image" },
      mask: { kind: "binary_mask", cardinality: "zero_or_one" },
      extras: { kind: "text", cardinality: "zero_or_more" },
    },
    inputBindings: { image: "pipeline.inputs.image" },
    parameterSchema: { name: "test", version: "1", operators: ["segment@1"] },
  });
  expect(html).not.toContain("缺少输入");
  expect(html).not.toContain("未连接：");
  expect(html).toContain('data-handle="mask"');
  expect(html).toContain("可选");
});

it("unavailable and ambiguous implementations never look configured", () => {
  expect(render({ ...base, adapters: [] })).toContain("尚未配置实现");
  expect(render({
    ...base,
    adapters: [{ value: "a", label: "A" }, { value: "b", label: "B" }],
  })).toContain("请选择模型实现");
  const unavailable = render({ ...base, backend: "removed" });
  expect(unavailable).toContain("当前实现未能匹配");
  expect(unavailable).not.toContain("配置已提供");
  expect(render({
    ...base,
    configurationBlockedReason: "高级 JSON 尚未应用",
  })).toContain("高级 JSON 尚未应用");
});

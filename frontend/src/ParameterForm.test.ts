import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ParameterForm } from "./ParameterForm";

const props = {
  adapter: {
    name: "resize",
    version: "1",
    operators: ["resize_image@1"],
    defaults: { width: 128, height: 128 },
    parameter_schema: {
      required: ["width"],
      properties: {
        width: { type: "integer", minimum: 1 },
        height: { type: "integer" },
      },
    },
  },
  parameters: { width: 64 },
  draft: {},
  errors: {},
  onChange: vi.fn(),
  setDraft: vi.fn(),
  setErrors: vi.fn(),
};
describe("parameter form presentations", () => {
  it("keeps editable values, source and accessible reset in compact node rows", () => {
    const html = renderToStaticMarkup(
      createElement(ParameterForm, { ...props, presentation: "node" }),
    );
    expect(html).toContain("parameter-form-node");
    expect(html).toContain("width（必填）");
    expect(html).toContain('value="64"');
    expect(html).toContain('aria-label="参数 width 来源"');
    expect(html).toContain("显式配置");
    expect(html).toContain("沿用实现默认值");
    expect(html).toContain('aria-label="恢复默认值 · width"');
    expect(html).toContain("↺");
    expect(html).not.toContain("下方编辑完整");
  });
  it("retains draft and validation alerts in node presentation", () => {
    const html = renderToStaticMarkup(
      createElement(ParameterForm, {
        ...props,
        presentation: "node",
        draft: { width: "bad" },
        errors: { width: "width 范围不合法" },
      }),
    );
    expect(html).toContain('value="bad"');
    expect(html).toContain("编辑尚未应用");
    expect(html).toContain("放弃字段编辑 · width");
    expect(html).toContain("width 范围不合法");
  });
  it("leaves Details explanations and full reset labels available", () => {
    const html = renderToStaticMarkup(createElement(ParameterForm, props));
    expect(html).toContain("parameter-form-details");
    expect(html).toContain("后端编译仍是最终校验");
    expect(html).toContain("下方编辑完整");
    expect(html).toContain(">恢复默认值 · width</button>");
  });
});

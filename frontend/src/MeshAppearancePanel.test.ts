import { describe, it, expect } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AppearanceBody, parseAppearance } from "./MeshAppearancePanel";
const base = {
  primitive_count: 1,
  textured_primitives: 0,
  vertex_colored_primitives: 0,
  postprocess_mode: null,
};
const render = (value: typeof base | ReturnType<typeof parseAppearance>) =>
  renderToStaticMarkup(
    createElement(AppearanceBody, { value: parseAppearance(value) }),
  );
describe("mesh appearance", () => {
  it("shows recorded fallback separately from successful execution", () => {
    const html = render({
      ...base,
      postprocess_mode: "geometry_fallback_no_texture",
    });
    expect(html).toContain("纹理后处理降级为纯几何输出");
    expect(html).not.toContain("执行失败");
  });
  it("does not invent a cause for legacy untextured output", () => {
    const html = render(base);
    expect(html).toContain("后处理原因未记录");
    expect(html).not.toContain("降级为纯几何输出");
  });
  it("distinguishes vertex colors from texture", () => {
    const html = render({ ...base, vertex_colored_primitives: 1 });
    expect(html).toContain("此输出未包含纹理");
    expect(html).toContain("含顶点颜色的网格部分：1/1");
    expect(render({ ...base, textured_primitives: 1 })).toContain(
      "含纹理的网格部分：1/1",
    );
  });
  it("rejects invalid evidence", () => {
    expect(() =>
      parseAppearance({ ...base, textured_primitives: 2 }),
    ).toThrow();
  });
});

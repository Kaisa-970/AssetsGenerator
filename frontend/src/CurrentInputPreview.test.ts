import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";

const outputPreview = vi.hoisted(() => vi.fn());
vi.mock("./HistoricalInputPreview", () => ({
  HistoricalInputPreview: (props: unknown) => {
    outputPreview(props);
    return createElement("span", null, "verified-output-preview");
  },
}));
import { CurrentInputPreview } from "./CurrentInputPreview";

const origin = {
  artifactId: "sha256:input",
  runId: "dag_original",
  nodeId: "input:image",
  port: "image",
  snapshot: "sha256:original-snapshot",
};
const render = (
  nodeId = origin.nodeId,
  artifactId = origin.artifactId,
  kind = "rgb_image",
) =>
  renderToStaticMarkup(
    createElement(CurrentInputPreview, {
      name: "image",
      kind,
      artifactId,
      origin: { ...origin, nodeId },
    }),
  );
beforeEach(() => outputPreview.mockClear());

it.each(["rgb_image", "rgba_image", "binary_mask"])(
  "mounts verified historical preview for an original %s input",
  (kind) => {
    expect(render(origin.nodeId, origin.artifactId, kind)).toContain(
      "verified-output-preview",
    );
    expect(outputPreview).toHaveBeenCalledWith({
      artifactId: origin.artifactId,
      origin,
    });
  },
);

it("keeps verified output preview for actual historical node outputs", () => {
  expect(render("encode")).toContain("verified-output-preview");
  expect(outputPreview).toHaveBeenCalledWith({
    artifactId: origin.artifactId,
    origin: { ...origin, nodeId: "encode" },
  });
});
it("does not attribute a replacement reference to the old input source", () => {
  const html = render(origin.nodeId, "sha256:replacement");
  expect(outputPreview).not.toHaveBeenCalled();
  expect(html).not.toContain(origin.runId);
  expect(html).not.toContain(origin.snapshot);
  expect(html).toContain("sha256:replacement");
});
it("keeps text on its existing verified text-read path", () => {
  expect(render("input:text", origin.artifactId, "text")).toContain(
    "正在读取已绑定文本",
  );
  expect(outputPreview).not.toHaveBeenCalled();
});

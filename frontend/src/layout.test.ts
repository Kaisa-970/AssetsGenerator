import { expect, it } from "vitest";
import { dependencyLayout, type Pipeline } from "./graph";
it("orders dependencies rather than JSON keys, including joins", () => {
  const p: Pipeline = { pipeline: "test", version: "1", inputs: { image: { kind: "rgb_image" } }, nodes: {
    extract: { operator: "x@1", inputs: { mask: "segment.outputs.mask", image: "pipeline.inputs.image", other: "branch.outputs.mask" } },
    branch: { operator: "x@1", inputs: { image: "pipeline.inputs.image" } },
    segment: { operator: "x@1", inputs: { image: "pipeline.inputs.image" } },
  }};
  const before = JSON.stringify(p), positions = dependencyLayout(p);
  expect(positions["input:image"].x).toBeLessThan(positions.segment.x);
  expect(positions.segment.x).toBeLessThan(positions.extract.x);
  expect(positions.branch.x).toBeLessThan(positions.extract.x);
  expect(positions.branch.y).not.toBe(positions.segment.y);
  expect(JSON.stringify(p)).toBe(before);
  expect(dependencyLayout({ ...p, nodes: Object.fromEntries(Object.entries(p.nodes).reverse()) })).toEqual(positions);
});
it("keeps cyclic drafts displayable", () => {
  const layout = dependencyLayout({pipeline:"cycle",version:"1",inputs:{},nodes:{
    a:{operator:"x@1",inputs:{i:"b.outputs.o"}}, b:{operator:"x@1",inputs:{i:"a.outputs.o"}}
  }});
  expect(layout.a).toBeDefined();
  expect(layout.b).toBeDefined();
  expect(layout.a.y).not.toBe(layout.b.y);
});

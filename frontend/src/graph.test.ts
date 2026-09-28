import { pipelineThrough, duplicateNode, backendLabel } from "./graph";
import { describe, it, expect } from "vitest";
import { load, dump } from "js-yaml";

it("keeps same-name model deployments distinguishable and unknown identities intact", () => {
  const catalog: Catalog = {
    operators: {},
    adapters: [],
    templates: [],
    model_services: [
      {
        backend: "model_a",
        display_name: "Mesh",
        endpoint: "http://a",
        operator: "shape@1",
        descriptor_digest: "a",
        frame_id: "native",
        up_axis: "+Z",
        unit: "relative",
      },
      {
        backend: "model_b",
        display_name: "Mesh",
        endpoint: "http://b",
        operator: "shape@1",
        descriptor_digest: "b",
        frame_id: "native",
        up_axis: "+Z",
        unit: "relative",
      },
    ],
  };
  expect(backendLabel(catalog, "model_a")).toBe("Mesh · model_a");
  expect(backendLabel(catalog, "model_b")).toBe("Mesh · model_b");
  expect(backendLabel(catalog, "unregistered")).toBe("unregistered");
});
import {
  bind,
  compatible,
  connectionError,
  graphEdges,
  removeNodes,
  validateDocument,
  type Pipeline,
  type Catalog,
} from "./graph";
const p: Pipeline = {
  pipeline: "diamond",
  version: "1",
  inputs: { image: { kind: "rgb_image" } },
  nodes: {
    a: {
      operator: "copy@1",
      inputs: { image: "pipeline.inputs.image" },
      parameters: { seed: 42 },
    },
    b: { operator: "copy@1", inputs: { image: "a.outputs.image?" } },
  },
};
const c: Catalog = {
  operators: {
    "copy@1": {
      name: "copy",
      version: "1",
      inputs: { image: { kind: "rgb_image" } },
      outputs: { image: { kind: "rgb_image" } },
    },
  },
  adapters: [],
  templates: [],
};
describe("editable graph contracts", () => {
  it("preserves optional edges, parameters and unknown metadata in YAML roundtrip", () => {
    const original = { ...p, custom: { policy: "explicit" } };
    expect(validateDocument(load(dump(original)))).toEqual(original);
    expect(graphEdges(original)[1].label).toBe("可选");
  });
  it("binds two instances independently and preserves upstream identity", () => {
    const next = bind(p, "a", "image", "b", "image");
    expect(next.nodes.b.inputs.image).toBe("a.outputs.image");
    expect(p.nodes.b.inputs.image).toBe("a.outputs.image?");
  });
  it("rejects cycles and incompatible carriers/cardinality/schema", () => {
    expect(connectionError(p, c, "b", "image", "a", "image")).toContain("循环");
    expect(
      compatible(
        { kind: "rgb_image", cardinality: "one_or_more" },
        { kind: "rgb_image" },
      ),
    ).toBeTruthy();
    expect(
      compatible(
        { kind: "rgb_image", carriers: ["structured"] },
        { kind: "rgb_image", carriers: ["artifact_ref"] },
      ),
    ).toBeTruthy();
    expect(
      compatible(
        { kind: "rgb_image" },
        { kind: "rgb_image", schema_name: "Foo" },
      ),
    ).toBeTruthy();
  });
  it("removes dependent bindings when their input/node disappears", () => {
    expect(removeNodes(p, new Set(["a"])).nodes.b.inputs).toEqual({});
    expect(removeNodes(p, new Set(["input:image"])).nodes.a.inputs).toEqual({});
  });
  it("rejects malformed imported documents", () => {
    expect(() => validateDocument({ pipeline: "x" })).toThrow();
    expect(() =>
      validateDocument({
        ...p,
        nodes: { a: { operator: "copy@1", inputs: { image: 12 } } },
      }),
    ).toThrow();
  });
});

import { readFileSync } from "node:fs";
it("loads real single-image and multi-view templates without changing bindings", () => {
  for (const name of ["dag-image-asset", "dag-multi-view-asset"]) {
    const original = load(
      readFileSync(
        new URL(`../../examples/${name}.yaml`, import.meta.url),
        "utf8",
      ),
    );
    const pipeline = validateDocument(original);
    expect(load(dump(pipeline))).toEqual(original);
    expect(graphEdges(pipeline).length).toBe(
      Object.values(pipeline.nodes).reduce(
        (count, node) => count + Object.keys(node.inputs).length,
        0,
      ),
    );
  }
});
it("supports catalog plural kinds and rejects ambiguous unions", () => {
  expect(
    compatible(
      { kinds: ["rgb_image"], carriers: ["artifact_ref"] },
      { kinds: ["rgb_image", "rgba_image"], carriers: ["artifact_ref"] },
    ),
  ).toBeNull();
  expect(
    compatible(
      { kinds: ["rgb_image", "rgba_image"] },
      { kinds: ["rgb_image"] },
    ),
  ).toBeTruthy();
});
it("retains shared references through a diamond join", () => {
  const diamond: Pipeline = {
    ...p,
    nodes: {
      a: { operator: "copy@1", inputs: { image: "pipeline.inputs.image" } },
      b: { operator: "copy@1", inputs: { image: "a.outputs.image" } },
      c: { operator: "copy@1", inputs: { image: "a.outputs.image" } },
      d: {
        operator: "join@1",
        inputs: { left: "b.outputs.image", right: "c.outputs.image" },
      },
    },
  };
  const restored = validateDocument(load(dump(diamond)));
  expect(
    graphEdges(restored).filter((edge) => edge.source === "a"),
  ).toHaveLength(2);
  expect(restored.nodes.b.inputs.image).toBe(restored.nodes.c.inputs.image);
  expect(
    graphEdges(restored).filter((edge) => edge.target === "d"),
  ).toHaveLength(2);
});

import { renameNode } from "./graph";
it("renames instance IDs while preserving optional bindings", () => {
  const renamed = renameNode(p, "a", "another");
  expect(renamed.nodes.b.inputs.image).toBe("another.outputs.image?");
  expect(
    renameNode(renamed, "input:image", "photo").nodes.another.inputs.image,
  ).toBe("pipeline.inputs.photo");
  expect(() => renameNode(p, "a", "b")).toThrow();
});

import { selectBackend, selectAdapter } from "./graph";
it("clears fixed remote bindings when switching profiles or returning to default", () => {
  const adapter = (name: string) => ({
    name: "remote_shape",
    version: "1",
    operators: ["shape@1"],
    backend: name,
    adapter: "remote_shape@1",
    parameter_schema: {
      properties: {
        remote_endpoint: { enum: [`http://${name}`] },
        service_id: { enum: [name] },
        backend_digest: { enum: [name] },
        pipeline_type: { enum: ["512", "1024"] },
      },
    },
  });
  const catalog: Catalog = {
    operators: {},
    templates: [],
    adapters: [adapter("default")],
    backends: [adapter("first"), adapter("second")],
  };
  const node = {
    operator: "shape@1",
    adapter: "remote_shape@1",
    backend: "first",
    inputs: {},
    parameters: {
      remote_endpoint: "http://first",
      service_id: "first",
      backend_digest: "first",
      seed: 7,
      pipeline_type: "512",
    },
  };
  for (const backend of ["second", ""]) {
    const changed = selectBackend(node, backend, catalog);
    expect(changed.parameters).toEqual({ seed: 7, pipeline_type: "512" });
    expect(changed.backend).toBe(backend || undefined);
  }
  expect(node.parameters.service_id).toBe("first");
  expect(
    selectBackend({ ...node, backend: "uninstalled" }, "second", catalog)
      .parameters,
  ).toEqual({ seed: 7, pipeline_type: "512" });
});

it("duplicate preserves configured upstreams but has independent nested values", () => {
  const pipeline: Pipeline = {
    pipeline: "copy",
    version: "1",
    inputs: {},
    nodes: {
      source: {
        operator: "op@1",
        adapter: "local@1",
        backend: "installed",
        inputs: { image: "upstream.outputs.image?" },
        parameters: { options: { seed: 7 } },
      },
      source_copy: { operator: "op@1", inputs: {} },
      consumer: { operator: "op@1", inputs: { image: "source.outputs.image" } },
    },
  };
  const result = duplicateNode(pipeline, "source");
  expect(result.id).toBe("source_copy_2");
  expect(result.pipeline.nodes[result.id]).toEqual(pipeline.nodes.source);
  expect(result.pipeline.nodes.consumer.inputs.image).toBe(
    "source.outputs.image",
  );
  (
    result.pipeline.nodes[result.id].parameters!.options as { seed: number }
  ).seed = 99;
  expect(pipeline.nodes.source.parameters!.options).toEqual({ seed: 7 });
  expect(pipeline.nodes[result.id]).toBeUndefined();
  expect(() => duplicateNode(pipeline, "missing")).toThrow();
});

it("backend switching selects compatible implementations and clears both fixed identities", () => {
  const a = {
    name: "a",
    version: "1",
    operators: ["op@1"],
    parameter_schema: {
      properties: { old_identity: { type: "string", enum: ["a"] } },
    },
  };
  const b = {
    name: "b",
    version: "1",
    operators: ["op@1"],
    parameter_schema: {
      properties: { new_identity: { type: "string", enum: ["b"] } },
    },
  };
  const catalog: Catalog = {
    operators: {},
    templates: [],
    adapters: [a],
    backends: [
      { ...a, adapter: "a@1", backend: "first" },
      { ...b, adapter: "b@1", backend: "second" },
    ],
  };
  const node = {
    operator: "op@1",
    adapter: "a@1",
    backend: "first",
    inputs: {},
    parameters: { old_identity: "a", new_identity: "stale", seed: 4 },
  };
  const changed = selectBackend(node, "second", catalog);
  expect(changed.adapter).toBe("b@1");
  expect(changed.parameters).toEqual({ seed: 4 });
  expect(selectBackend(changed, "", catalog).adapter).toBe("a@1");
  catalog.backends!.push(
    { ...a, adapter: "a@1", backend: "ambiguous" },
    { ...b, adapter: "b@1", backend: "ambiguous" },
  );
  expect(
    selectBackend({ ...node, adapter: undefined }, "ambiguous", catalog)
      .adapter,
  ).toBeUndefined();
});

it("direct adapter selection clears fixed values while preserving editable parameters", () => {
  const adapter = (name: string) => ({
    name,
    version: "1",
    operators: ["op@1"],
    parameter_schema: {
      properties: { [name]: { type: "string", enum: [name] } },
    },
  });
  const catalog: Catalog = {
    operators: {},
    templates: [],
    adapters: [adapter("a"), adapter("b")],
  };
  const node = {
    operator: "op@1",
    adapter: "a@1",
    inputs: {},
    parameters: { a: "a", b: "stale", seed: 42 },
  };
  expect(selectAdapter(node, "b@1", catalog)).toEqual({
    ...node,
    adapter: "b@1",
    parameters: { seed: 42 },
  });
  expect(node.parameters).toEqual({ a: "a", b: "stale", seed: 42 });
  catalog.backends = catalog.adapters.map((item) => ({
    ...item,
    backend: "shared",
    adapter: `${item.name}@1`,
  }));
  expect(
    selectAdapter({ ...node, backend: "shared" }, "b@1", catalog).parameters,
  ).toEqual({ seed: 42 });
});

it.each([
  { nodes: "abc" },
  { inputs: 5 },
  { nodes: { bad: { operator: "copy@1", inputs: [] } } },
  { nodes: { bad: { operator: "copy@1", inputs: {}, parameters: [] } } },
  { inputs: { image: { kinds: "rgb_image" } } },
  { inputs: { image: { kinds: [42] } } },
  { inputs: { image: { kind: "rgb_image", carriers: "artifact_ref" } } },
])("rejects unsafe imported document structure: %j", (invalid) => {
  expect(() => validateDocument({ ...p, ...invalid })).toThrow();
  expect(p.nodes.a.parameters).toEqual({ seed: 42 });
});

it("connection diagnostics identify endpoints and declared schema values", () => {
  const catalog: Catalog = structuredClone(c);
  catalog.operators["copy@1"].inputs.image.schema_name = "png";
  const original = JSON.stringify(p);
  expect(
    connectionError(p, catalog, "input:image", "value", "a", "image"),
  ).toBe("input:image.value → a.image：schema_name 不匹配：未声明 → png");
  expect(
    compatible(
      { kind: "rgb_image", schema_name: "raster_image" },
      { kind: "rgb_image", schema_name: "png" },
    ),
  ).toContain("raster_image → png");
  expect(
    compatible(
      { kind: "rgb_image", cardinality: "zero_or_one" },
      { kind: "rgb_image" },
    ),
  ).toContain("zero_or_one → one");
  expect(JSON.stringify(p)).toBe(original);
});

it("run to target includes shared ancestors once and excludes downstream", () => {
  const graph: Pipeline = {
    pipeline: "slice",
    version: "1",
    inputs: { image: { kind: "rgb_image" }, unused: { kind: "rgb_image" } },
    nodes: {
      a: { operator: "copy@1", inputs: { image: "pipeline.inputs.image" } },
      b: { operator: "copy@1", inputs: { image: "a.outputs.image" } },
      c: { operator: "copy@1", inputs: { image: "a.outputs.image" } },
      d: {
        operator: "join@1",
        inputs: { left: "b.outputs.image", right: "c.outputs.image" },
      },
      end: { operator: "copy@1", inputs: { image: "d.outputs.image" } },
    },
  };
  const sliced = pipelineThrough(graph, "d");
  expect(Object.keys(sliced.nodes)).toEqual(["a", "b", "c", "d"]);
  expect(Object.keys(sliced.inputs)).toEqual(["image"]);
  expect(Object.keys(graph.nodes)).toHaveLength(5);
});

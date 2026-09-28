import { describe, expect, it } from "vitest";
import { inputReferenceMismatch } from "./InputHistorySelector";
const output = {
  node_id: "encode",
  port: "image",
  kind: "rgb_image",
  url: "/image",
};
const source = {
  source_run_id: "run",
  node_id: "encode",
  port: "image",
  kind: "rgb_image",
  schema_name: "png",
  schema_version: "1.0",
  reference: { artifact_id: "exact" },
};
describe("historical input eligibility", () => {
  it("checks declared spatial metadata without assuming frame conversion", () => {
    const spatial = { ...source, frame_id: "canonical", unit: "meter" };
    const port = {
      kind: "rgb_image",
      requires_frame: true,
      requires_unit: true,
    };
    expect(
      inputReferenceMismatch(spatial, "run", output, port),
    ).toBeUndefined();
    expect(
      inputReferenceMismatch({ ...spatial, unit: null }, "run", output, port),
    ).toBeDefined();
    expect(
      inputReferenceMismatch(spatial, "run", output, {
        ...port,
        frame_id: "camera",
      }),
    ).toBeDefined();
    expect(
      inputReferenceMismatch(spatial, "run", output, {
        ...port,
        unit: "relative_unit",
      }),
    ).toBeDefined();
  });
  it("accepts exact scalar identity and schema", () => {
    expect(
      inputReferenceMismatch(source, "run", output, {
        kind: "rgb_image",
        schema_name: "png",
        schema_version: "1.0",
      }),
    ).toBeUndefined();
  });
  it("rejects foreign identity, schema, collection and unverified space", () => {
    expect(
      inputReferenceMismatch(
        { ...source, source_run_id: "other" },
        "run",
        output,
        { kind: "rgb_image" },
      ),
    ).toBeDefined();
    for (const extra of [
      { schema_name: "raster_image" },
      { schema_version: "2.0" },
      { carriers: ["structured"] },
      { cardinality: "one_or_more" },
      { frame_id: "camera" },
      { requires_unit: true },
    ]) {
      expect(
        inputReferenceMismatch(source, "run", output, {
          kind: "rgb_image",
          ...extra,
        }),
      ).toBeDefined();
    }
  });
});

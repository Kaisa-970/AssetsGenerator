import { describe, expect, it } from "vitest";
import {
  comparisonInputCompatible,
  comparisonOutputUrl,
  comparisonSelectionMatches,
  type ComparisonSlot,
} from "./OutputComparison";

const slot: ComparisonSlot = {
  runId: "run_a",
  nodeId: "node",
  port: "mask",
  kind: "binary_mask",
  snapshot: "sha256:fixed",
  reference: { artifact_id: "sha256:mask" },
  schema_name: "png",
  schema_version: "1.0",
};
describe("fixed output comparison", () => {
  it("qualifies scalar Artifact inputs by kind and declared schema", () => {
    expect(
      comparisonInputCompatible(slot, {
        kind: "binary_mask",
        schema_name: "png",
        schema_version: "1.0",
        carriers: ["artifact_ref"],
      }),
    ).toBe(true);
    for (const input of [
      { kind: "rgb_image" },
      { kind: "binary_mask", schema_name: "other" },
      { kind: "binary_mask", schema_version: "2" },
      { kind: "binary_mask", carriers: ["structured"] },
      { kind: "binary_mask", cardinality: "many" },
    ])
      expect(comparisonInputCompatible(slot, input)).toBe(false);
  });
  it("never equates equal artifacts from different source snapshots", () => {
    const selection = {
      artifactId: slot.reference.artifact_id,
      runId: slot.runId,
      nodeId: slot.nodeId,
      port: slot.port,
      snapshot: slot.snapshot,
    };
    expect(comparisonSelectionMatches(slot, selection)).toBe(true);
    expect(
      comparisonSelectionMatches(slot, { artifactId: selection.artifactId }),
    ).toBe(false);
    for (const key of ["runId", "nodeId", "port", "snapshot"] as const)
      expect(
        comparisonSelectionMatches(slot, { ...selection, [key]: "other" }),
      ).toBe(false);
  });
  it("requests immutable snapshot output with encoded path components", () => {
    expect(comparisonOutputUrl({ ...slot, nodeId: "node/unsafe" })).toBe(
      "/api/runs/run_a/snapshot-output/node%2Funsafe/mask?snapshot=sha256%3Afixed",
    );
  });
});

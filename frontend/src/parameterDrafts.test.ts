import { describe, expect, it } from "vitest";
import {
  emptyParameterDraft,
  pendingParameterNodes,
  parseParameterObject,
} from "./parameterDrafts";
describe("unapplied parameter drafts", () => {
  it("blocks pending JSON and fields across nodes, ignores deleted nodes and error-only records", () => {
    expect(
      pendingParameterNodes(
        {
          a: { ...emptyParameterDraft(), json: "{" },
          b: { fields: { width: "bad" }, errors: { width: "invalid" } },
          deleted: { ...emptyParameterDraft(), json: "{}" },
          clean: { fields: {}, errors: { width: "old" } },
        },
        { a: {}, b: {}, clean: {} },
      ),
    ).toEqual(["a", "b"]);
  });
  it("accepts parameter objects and rejects invalid or non-object JSON", () => {
    expect(parseParameterObject('{"width":8}')).toEqual({ width: 8 });
    for (const input of ["{", "[]", "null", "2"])
      expect(() => parseParameterObject(input)).toThrow();
  });
});

import { describe, expect, it } from "vitest";
import { validateGlb } from "./GlbPreview";
function glb(document: object) {
  const text = JSON.stringify(document);
  const json = new TextEncoder().encode(
    text.padEnd(Math.ceil(text.length / 4) * 4),
  );
  const bytes = new ArrayBuffer(20 + json.length);
  const view = new DataView(bytes);
  [0x46546c67, 2, bytes.byteLength, json.length, 0x4e4f534a].forEach((v, i) =>
    view.setUint32(i * 4, v, true),
  );
  new Uint8Array(bytes, 20).set(json);
  return bytes;
}
describe("self-contained preview boundary", () => {
  it("accepts bufferView resources", () =>
    expect(
      validateGlb(
        glb({
          asset: { version: "2.0" },
          buffers: [{ byteLength: 16 }],
          images: [{ bufferView: 0, mimeType: "image/png" }],
        }),
      ).asset.version,
    ).toBe("2.0"));
  it.each([
    "https://example.com/a.png",
    "file:///tmp/a",
    "data:image/png;base64,eA==",
  ])("rejects resource %s", (uri) =>
    expect(() => validateGlb(glb({ images: [{ uri }] }))).toThrow("内嵌资源"),
  );
  it("rejects invalid bytes and inconsistent length", () => {
    expect(() => validateGlb(new ArrayBuffer(4))).toThrow();
    const bytes = glb({});
    new DataView(bytes).setUint32(8, 0, true);
    expect(() => validateGlb(bytes)).toThrow();
  });
});

import { expect, it, vi } from "vitest";
import { newRequestId } from "./requestId";
it("creates UUIDs with only getRandomValues available on LAN HTTP", () => {
  const random = globalThis.crypto;
  vi.stubGlobal("crypto", {
    getRandomValues: random.getRandomValues.bind(random),
  });
  try {
    const ids = Array.from({ length: 100 }, () => newRequestId());
    expect(new Set(ids).size).toBe(100);
    for (const id of ids)
      expect(id).toMatch(
        /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/,
      );
  } finally {
    vi.unstubAllGlobals();
  }
});

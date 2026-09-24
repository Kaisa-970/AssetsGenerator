import { it, expect } from "vitest";
import { selectedPreviewPort, previewEmptyMessage } from "./selectedPreview";
it("keeps the selected port and rendered output consistent across nodes", () => {
  expect(
    selectedPreviewPort([{ port: "candidates" }, { port: "mask" }], ""),
  ).toBe("mask");
  expect(selectedPreviewPort([{ port: "image" }], "mask")).toBe("image");
  expect(selectedPreviewPort([], "image")).toBe("");
  expect(
    selectedPreviewPort(
      [{ port: "candidates" }, { port: "mask" }],
      "candidates",
    ),
  ).toBe("candidates");
});
it("distinguishes absent history, unfinished nodes and failed execution", () => {
  expect(previewEmptyMessage("n", undefined, undefined)).toContain(
    "尚未选择运行",
  );
  expect(previewEmptyMessage("n", "r", undefined)).toContain("没有此节点");
  for (const [status, label] of [
    ["running", "正在运行"],
    ["pending", "待运行"],
    ["failed", "失败"],
    ["recovery_blocked", "证据受阻"],
    ["succeeded", "已完成"],
  ])
    expect(previewEmptyMessage("n", "r", status)).toContain(label);
});

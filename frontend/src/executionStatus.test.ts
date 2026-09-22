import { expect, test } from "vitest";
import { executionStatus } from "./executionStatus";

test("execution labels distinguish failure, interruption and evidence blocks", () => {
  expect(executionStatus("failed")).toBe("失败");
  expect(executionStatus("interrupted")).toBe("中断");
  expect(executionStatus("recovery_blocked")).toBe("证据受阻");
  expect(executionStatus("succeeded")).toBe("完成");
  expect(executionStatus("skipped")).toBe("状态无法核实");
  expect(executionStatus()).toBe("状态无法核实");
});

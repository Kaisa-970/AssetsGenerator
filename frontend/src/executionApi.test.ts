import { describe, expect, it } from "vitest";
import { executionFailureSummary } from "./executionApi";

describe("execution failure summaries", () => {
  it("maps CUDA memory failures to an actionable Chinese hint", () => {
    expect(
      executionFailureSummary({
        status: "failed",
        attempts: [
          {
            error_code: "backend_failed",
            error_detail: "CUDA driver error: out of memory",
          },
        ],
      }),
    ).toContain("显存不足");
  });
  it("does not invent a cause when only a backend code is available", () => {
    expect(
      executionFailureSummary({
        status: "failed",
        attempts: [{ error_code: "backend_failed" }],
      }),
    ).toBe("模型执行失败 · 查看运行记录");
  });
});

it.each([
  "CUDA driver error: initialization failed",
  "CUDA driver error: illegal memory access",
  "CUDA driver error: insufficient driver version",
])("does not label %s as OOM", (error_detail) => {
  expect(
    executionFailureSummary({
      status: "failed",
      attempts: [{ error_code: "backend_failed", error_detail }],
    }),
  ).toBe("模型执行失败 · 查看运行记录");
});
it("prioritizes unknown remote status over memory error text", () => {
  expect(
    executionFailureSummary({
      status: "running",
      attempts: [
        {
          error_code: "remote_transport_unknown",
          error_detail: "CUDA out of memory",
        },
      ],
    }),
  ).toBe("远端状态未知 · 先核实作业");
});

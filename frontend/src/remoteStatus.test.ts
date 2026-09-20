import { expect, test } from "vitest";
import { remoteStatusMessage } from "./remoteStatus";

test("remote messages distinguish recorded state from current service state", () => {
  expect(remoteStatusMessage("remote_queued")).toContain("上次报告作业已排队");
  expect(remoteStatusMessage("remote_running")).toContain("上次报告作业正在执行");
  expect(remoteStatusMessage("remote_transport_unknown")).toContain("不要据此重复提交");
  expect(remoteStatusMessage(undefined)).toContain("核实结果");
});

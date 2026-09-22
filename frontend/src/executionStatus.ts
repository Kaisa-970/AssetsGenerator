/** Execution facts only; QA, freshness and reuse are separate dimensions. */
const labels: Record<string, string> = {
  pending: "待运行",
  running: "运行中",
  waiting_for_input: "等待人工",
  succeeded: "完成",
  failed: "失败",
  interrupted: "中断",
  blocked: "上游未完成",
  recovery_blocked: "证据受阻",
};
export function executionStatus(status?: string): string {
  return (status && labels[status]) || "状态无法核实";
}

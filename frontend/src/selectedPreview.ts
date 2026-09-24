import { executionStatus } from "./executionStatus";
export function selectedPreviewPort(
  outputs: { port: string }[],
  requested: string,
): string {
  return (
    outputs.find((o) => o.port === requested)?.port ||
    outputs.find((o) => o.port === "mask")?.port ||
    outputs[0]?.port ||
    ""
  );
}
export function previewEmptyMessage(
  nodeId: string | undefined,
  runId: string | undefined,
  status: string | undefined,
): string {
  if (!nodeId) return "请选择节点查看输出。";
  if (!runId)
    return "尚未选择运行。启动管线或选择已有运行后，可查看此节点结果。";
  if (!status)
    return "所选历史运行没有此节点。当前草稿与该运行的固定计划不同。";
  if (status === "succeeded") return "此节点已完成，但没有可展示的输出。";
  if (status === "running")
    return "此节点正在运行，结果尚未发布。查看不会重复执行模型。";
  if (status === "pending" || status === "blocked")
    return `此节点${executionStatus(status)}，尚无输出。`;
  if (status === "waiting_for_input")
    return "此节点等待人工决定，请在运行面板处理待办。";
  return `此节点${executionStatus(status)}，请在运行面板查看原因及允许的操作。`;
}

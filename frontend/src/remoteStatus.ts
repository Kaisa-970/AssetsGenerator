export function remoteStatusMessage(code?: string): string {
  if (code === "remote_queued")
    return "远程服务上次报告作业已排队。请在服务端执行此作业，完成后点击“恢复 / 继续此运行”。";
  if (code === "remote_running")
    return "远程服务上次报告作业正在执行。完成后点击“恢复 / 继续此运行”查询结果并继续后续节点。";
  if (code === "remote_transport_unknown")
    return "尚不能确认远程作业状态。请检查服务连接，再点击“恢复 / 继续此运行”核实原作业；不要据此重复提交。";
  return "远程作业已绑定到此节点。点击“恢复 / 继续此运行”核实结果。";
}

# 真实模型执行期间 HTTP 监听服务中断验收

日期：2026-09-21，产品代码基线 `52560ce`。使用现有 TRELLIS.2 环境和权重，
独立服务数据库、Store 与工作目录。无下载、无并行 GPU 任务。

## 事实

真实 CLI serve 与 execute 使用两个独立进程。父 DAG 提交单个 RGBA shape 作业后，
确认已登记 launcher 下存在真实 trellis2_runner.py 子进程且进程组 alive，再对
HTTP serve 进程发送 SIGKILL。退出码为 -9，执行器和模型进程保持运行。

父 DAG 显式继续时，shape 保持 running，错误提示为 remote_transport_unknown，
保留相同远程绑定及唯一 attempt；显式 retry 被拒绝，数据库作业仍为 running。
重新启动 CLI serve，核验相同部署身份后使用原端口、原数据库。父 DAG 重连认领
原作业，没有新提交键或第二个模型执行器。

原 execute 最终正常退出并提交成功结果。运行
`dag_5515c3d20df84fbeb07041f275dd7f1d` 发布成功，五个节点各一次 attempt，
服务数据库只有一个作业。父运行完整 Artifact 证据闭包通过校验。
GLB 46,600,248 字节，trimesh 回读 1,285,164 顶点、2,598,104 三角面。

再杀死重启后的监听进程，从本地证据恢复成功运行，完整节点记录保持不变。
独立检查重新比较初始/完成绑定、所有 attempt 数和离线恢复记录，并确认两个
监听进程及执行器均已退出。主验收退出码 0。

## 证据与边界

仓库外 `<DATASET_ROOT>/http-listener-crash-real-v1/` 包含 `verify.py`、`verify.log`、
两次监听服务的 PID/endpoint/日志、执行器 PID/日志、`submitted.json`、
`interrupted.json`、`offline.json`、`reconnected.json`、`completed.json`、
`restored-offline.json`、`validation.json` 及 Store/服务数据库。脚本拒绝已有数据库，
不能用重新跑脚本替代恢复原作业。

本项是 Core API 与真实 CLI 服务验收，不是浏览器故障恢复操作验收。
杀死的是 HTTP 监听服务，没有杀死执行器或模型；执行器故障重试见独立报告。
中断发生在真实 runner 加载/执行窗口，未使用采样进度证明中断时已进入 GPU sampling。
不覆盖模型进程崩溃、远端主机宕机、网络分区或多视图推理中断，也不作质量结论。
本提交只新增运行报告及更新状态，没有重复完整代码回归；git diff --check 通过。

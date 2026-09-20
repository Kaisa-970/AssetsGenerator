# 真实 TRELLIS2 推理中断验收

日期：2026-09-20。验证对象是通用 DAG 的本地主控进程异常退出，不是 GPU 驱动故障或模型断点续算。

## 方法与证据

复用真实单图 smoke 中用户已确认的 SelectionInputBinding，在同一 Store 创建独立运行目录和单节点生成 DAG。引用原选择证据，不新增人工决定、不代签用户。使用既有 TRELLTS 环境和本地权重，没有下载模型或 PyTorch。

验证脚本加载真实 profile 后 fork 独立主控进程执行 DagEngine；监督进程保留相同配置。确认 TRELLIS2 已完成 sparse structure 采样、Backend 仍活跃后，只向该主控 PID 发送 SIGKILL。launcher 与 Backend 继续运行，恢复使用新 Repository/Engine 实例重新加载耐久记录。此方法验证主控死亡及锁释放，但不重复验证冷启动 profile 加载；正常服务重启另见 [真实 smoke](generic-dag-real-smoke.md)。

运行 ID：`dag_de2b0f59947c401db70a9eff3cacc995`。

仓库外证据位于 `<DATASET_ROOT>/dag-interruption-20260920/`：

- `validate.py`：本次可复查的故障注入脚本，包含本机资源路径；不作为通用 CLI。
- `before-kill.json`、`kill.json`、`inference-progress.txt`：主控与 worker 身份及采样日志。
- `alive-recovered.json`、`blocked-retry.json`、`blocked-retry.txt`：原进程仍活跃时的恢复与重试阻塞。
- `exit-observation.json`、`exited-recovered.json`：原进程组退出、阻塞解除后的快照。
- `retried.json`、`validation.json`：显式重试成功记录及 GLB 回读统计。
- `startup-timings.json`、`validation.log`：身份核验耗时和验证日志。

## 已核实行为

- 主控退出时已有一个生成 attempt；原进程仍活跃时恢复为 recovery_blocked，节点 attempt 为 interrupted。
- 同阶段调用 retry 被准入门控拒绝，仍只有一个 attempt 和一条 worker 记录，没有启动第二个模型。
- 原进程组为空后，恢复解除 dispatch block，保留 interrupted；没有自动重新推理，也没有将未发布的临时模型文件视作成功。
- 显式 retry 创建第二个 attempt 并成功发布。第一条 worker 为 `job_65202138ab8a4e59924905c19b624d5a`，第二条为 `job_8f4a25350428416299c55fee0555de40`；第一次 attempt 保持 interrupted，第二次 succeeded，只有两次模型调用且未重叠。
- 发布输出的完整 Artifact 引用闭包通过验证；GLB 回读成功，1 个 geometry、181484 顶点、282529 三角面。原 SAM/人工选择/生成 smoke 的 node_states 与完成快照一致，未重新运行。

本轮只修改报告与指南，`git diff --check` 通过；没有重复运行全量单元测试。前一修复提交的 847 项测试结果不计作本轮新执行。

## 启动耗时观察

本次 profile 初始化约 45.00 秒，其中 SAM 环境身份核验约 10.51 秒、TRELLIS 环境约 10.37 秒、模型身份约 19.12 秒，SAM checkpoint 摘要约 2.75 秒。计时包装器只记录调用耗时，没有更改身份算法或跳过校验。

这定位了本次主要开销，但未重现此前约 6 分钟的服务启动延迟；不能将差异确定归因于冷缓存。运行期间存在系统内存及 swap 压力，本次不作为性能 benchmark。不通过弱化内容摘要或环境校验优化启动。

## 边界

此项不证明 GPU 驱动故障恢复、模型中间状态续算、脱离原进程组的 Backend、HTTP 作业恢复或完整里程碑 A 完成。A3 仍需多视图迁移及空间关系 validator，之后才接生产画布。

# DA3 多视图推理中主控中断验收

日期：2026-09-21。基线为 `b6ee3e8` 加本轮共享基础模块迁移；没有改变 DAG 状态机、
模型 runner、推理参数或旧证据。使用既有 DA3-Base、DA3/sugar 环境、SOH 双图，
GPU 串行；没有下载 PyTorch、模型或数据。

## 已验证

最终运行 `dag_19835b24b20c473badf7aa84f60e69d7`：独立主控执行 geometry →
reconstruction → release。监督进程观察真实 DA3 runner 的日志：已加载模型、
完成输入处理，但尚无 `Model Forward Pass Done`，随后只向自己创建的主控 PID
发送 SIGKILL。使用 `PYTHONUNBUFFERED=1` 避免将缓冲后一起输出的日志误判为时序。
这是推理调用期间的进程级故障注入，不声称锁定了某个 GPU kernel。

- 旧进程仍活跃时，恢复为 recovery_blocked；显式 retry 被拒绝，只有一个 geometry
  attempt 和一条 worker，不重复派发模型。
- 旧进程组自然退出后，恢复为 interrupted，派发阻塞解除，没有自动重算。
- 显式 retry 后三节点成功：geometry 两次 attempt，reconstruction/release 各一次。
- 所有当前输出（含集合中的 ArtifactRef）的完整引用闭包通过检查。
- 再次恢复保持 node_states 不变；发布 GLB 可回读，70,740 顶点、96,610 三角面。

仓库外证据位于 `<DATASET_ROOT>/multiview-interruption-20260921-v3/`：
`created.json`、`before-kill.json`、`kill.json`、`inference-progress.txt`、
`alive-recovered.json`、`blocked-retry.json`、`exit-observation.json`、
`exited-recovered.json`、`retried.json`、`restored.json`、`validation.json` 和独立 Store。

## 复现

从工作树运行下列脚本；资源配置格式与多视图编辑器相同，output 必须是不存在的
仓库外目录。脚本不会安装依赖；只终止自身创建的主控，允许原模型自然退出。

```bash
PYTHONPATH=src python examples/multiview_interruption_smoke.py \
  --config <MULTIVIEW_CONFIG.json> \
  --images <IMAGE_1.png> <IMAGE_2.png> \
  --output <NEW_EVIDENCE_DIRECTORY>
```

脚本以失败退出时不算通过；保留已写的证据。超时后不会自动重新推理，
检查 `final-worker-observation.json` 确认原进程状态后再决定下一步。

## 前两次尝试与边界

v1 在 DA3 前向完成后、后处理尚未结束时终止主控；门控与显式重试成功，最后验收
脚本误把集合当单个引用。修正后用独立进程只读验证发布和恢复，保留原失败记录。
它不计入前向阶段中断验收。v2 收紧触发条件但日志仍缓冲，错过窗口，脚本明确拒绝
验收。v3 关闭日志缓冲后完整脚本退出码 0，不修改前两次运行或用其填补最终断言。

本项覆盖 DA3 推理调用中的 DAG 主控崩溃，不覆盖 Open3D 重建中断、HTTP 监听服务
中断、主机断电、GPU 驱动故障、模型断点续算或代表性物体质量；监督进程复用已加载
资源 profile。独立冷启动身份验证曾在 v1 的只读复核执行，不能替代所有崩溃场景。

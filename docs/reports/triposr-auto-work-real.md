# TripoSR 自动执行循环验收

2026-09-23；基础提交 `6462af9`，work 实现为其后未提交工作区。

新增显式服务端 `remote_shape_cli work`，配置启动后持续串行领取任务。编辑器现有
远程提交/结果轮询机制保持不变；Core 没有远端 shell 或模型执行分支。

真实浏览器运行 `dag_5087845ca39a4aa6ad04860554a06ae8`：通过服务发现添加/复用
TripoSR，使用五节点发布模板和历史同一 RGBA 输入。启动后正式 work 命令自动领取
并完成 GPU 任务，本次没有执行 execute/drain 或临时执行器。五节点各一次 attempt，
最终 GLB 42,257 顶点、84,383 三角面、保留顶点颜色；声明和实际 frame 均为
triposr_glb_native/+Z/relative_unit。显式恢复后完整 node_states 保持一致。

25 项针对性测试通过，覆盖已知失败后继续、未知执行保留 running 并阻塞下一任务、
成功后重启不重跑、空闲不调用 handler、CLI 信号恢复与轮询参数验证，以及既有
服务 worker/process 回归。限定 Ruff/mypy 和 git diff --check 通过。

证据：`<DATASET_ROOT>/triposr-auto-work-real-v1/` 的 browser.cjs、browser.log、
submitted.json、completed.json、restored.json、verify.py、validation.json、quality.json、
截图与 visual.glb。服务使用上一轮 `<DATASET_ROOT>/triposr-discovery-real-v1/` 的
独立数据库/Store/workspace；work-loop.log 记录自动领取结果。

限制：未真实强杀本轮 GPU worker；未重跑 TRELLIS.2 比较；不证明视觉质量。
服务数据库之间不共享 GPU 锁，多模型 GPU 验收仍需串行。部署身份校验失败时，当前任务记录 failed/deployment_invalid，work 停止领取，后续任务保持 queued；需要管理员核实部署后重启。进程状态未知保持 running 并阻塞；持久化异常不擅自改写状态。

审查后补充身份失效回归：校验返回另一身份或抛出资源变化异常，均停止领取；不会把队列逐个标记失败。此修正为 CPU 测试验证，未重新运行真实 GPU。

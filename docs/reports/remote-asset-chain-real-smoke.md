# 远程 TRELLIS.2 五节点资产链真实 smoke

代码基线：`f2b35e8`。本报告证明流程运行及完成后离线恢复，不证明代表性物体质量，
不关闭远程 Backend 完整里程碑。未验收本链推理中异常退出、编辑器服务目录或后台作业队列。

## 输入与运行

复用已有 TRELLIS.2 独立 conda 环境和 4B 权重，无 PyTorch/权重下载。
输入复用此前确认的 prepared RGBA Artifact：
`sha256:c96ca95632d8685bf6b5267cd8b75e5d6941297f4885a967435e58588bcf8aa5`。

Pipeline：`pipelines/remote_shape_asset_v1.yaml`。
Run：`dag_e9c3fe81b4a34f21929dedbd7e2417ae`。
Service job：`dag_907121e972208e04facebcf8e71ff2e2f4d3450eb7a772842e7485a90714f652`。
Backend identity：`sha256:37c8a4a8e1711a6337c339ae4926f11d807b4d4ee1b4c7fac365cbe506101c5e`。

服务 HTTP 上传、持久化作业、独立模型进程、Core 导入及以下五节点全部成功：
shape → canonical → quality → assemble → publish。每个节点恰好一次 attempt。
数据库中恰好一个作业、一个 worker；worker exit code 0，进程组确认 exited。

## 交付与恢复证据

GLB：16,302,972 bytes，177,844 vertices，281,641 faces，回读有一个 base-color texture。
GLB Artifact：`sha256:fb5c2227f82e18942c48760dcab504da3f3600e5383d532e642dcaec4be53777`。
Release Artifact：`sha256:745dfdd1b3437839bb08a940af07f84679ac053d4bac5ff3e2eff886dbbd1f3b`。
QA overall 为 warn；不将节点成功或纹理存在解释为视觉质量验收。

服务停止后重新加载并 drain 已完成运行，所有 node_states 与首次 completed 快照完全相同。
完整 BuildRun 和 Release 的引用闭包通过 digest 核验，无再次模型执行。
验收结束后服务和模型进程均已退出。

外部证据放在 `<DATASET_ROOT>/remote-asset-chain-validation/`：config.json、endpoint.json、
submit.py、recover.py、verify.py、submitted.json、completed.json、restored.json、validation.json、
execute.json、service.sqlite、Store 与 visual.glb。completed 不被恢复脚本覆盖。
脚本为开发验收操作，不记录为用户人工批准。

## 代码检查

全量 pytest：1083 passed、1 failed（452.04s）；失败为历史配置摘要测试仍假设新增
Operator/关系契约不改变当前配置。后续测试修订显式重建历史契约，保留原三个摘要，
同时断言规范化关系改变计划身份、无关新增 Operator 不改变现有计划身份。
不将此次全量运行记录为全绿，也不把后续定向通过数相加。
全仓 Ruff check/format、mypy（108 source files）、build 成功。

测试与模型加载同时进行且出现内存交换，本次时长不作推理性能 benchmark。

测试修订后定向验证：`test_compilation_compatibility.py` 与 `test_compiled_plan.py`
共 35 passed（1.36s）；未重跑完整套件。

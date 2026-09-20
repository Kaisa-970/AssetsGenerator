# TRELLIS.2 远程 shape 真实 smoke

日期：2026-09-20。代码基线 `5131857`（随后无实现改动）。本报告验收单个远程 shape 节点，不关闭完整 Milestone C 或完整资产发布链。

## 实际执行

复用已有 TRELLIS.2 独立 conda 环境与本地模型快照，未下载模型或 PyTorch。shape-only 配置成功核验代码、模型和环境，启动本机耐久 HTTP 服务。输入复用此前单图验收的已确认 RGBA Artifact，未重新运行 SAM。

DAG 上传 RGBA 并登记 queued job；另一命令显式 execute，ServiceProcessWorker 保存身份和授权后启动 TRELLIS.2。服务确认进程组退出、exit_code=0 后封装结果；Core 下载、校验 GLB 与 NativeFrame、导入并生成节点 provenance。

- run：`dag_abe45ca2b95948428c87ea2fb138a5fb`
- job：`dag_19da50a09bc5516c2c0c68a3593e469b6fe1ddb595922bf8b553096578f3258b`
- 单节点一次 attempt，服务数据库一个 job、一个 worker。
- GLB：16,122,112 bytes；176,463 vertices；280,452 faces。
- 完整 BuildRun Artifact 证据闭包校验通过。

随后 SIGINT 关闭本次测试 HTTP 服务，执行句柄确认正常退出，端口不可连接；离线恢复仍为 succeeded。再次单独保存恢复前快照，与恢复后 node_states 比较一致。没有重新启动模型。

## 证据与限制

证据位于 `<DATASET_ROOT>/remote-shape-validation/`：config.json、endpoint.json、submitted.json、before-offline.json、completed.json、restored.json、validation.json、service.sqlite、store、native.glb，以及 submit.py/recover.py/verify.py。均不提交仓库。recover.py 会更新 completed.json，因此严格前后比较使用独立 before-offline.json，不能把同一脚本覆盖后的两个相同快照当作原始比较证据。

这证明真实 TRELLIS.2 的远程 shape 推理与完成后离线恢复，未验证推理中服务 SIGKILL 的真实模型恢复、编辑器远程配置、自动队列、最终 canonicalization/QA/release 或质量 benchmark。当前仅回读几何统计及证据校验，没有新的人工视觉质量结论。TripoSR 远程推理尚未验收。

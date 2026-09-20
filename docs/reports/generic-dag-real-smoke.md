# 单图 YAML DAG 真实模型 smoke

日期：2026-09-20。状态：真实 SAM → 人工选择 → TRELLIS2 → GLB 已成功；不关闭完整里程碑 A。

## 事实

使用既有 sugar/TRELLTS 环境和本地模型缓存，通过 `examples/dag-image-asset.yaml` 执行。没有安装或下载 PyTorch/模型。输入为既有 robot 图片。人工选择由用户在 DAG 审查页提交，不由自动 smoke 代签。

- 父运行 `dag_13536ba2abe146d19a1ca26ce970dd99` 状态 succeeded。
- SAM 产生 10 个候选；候选、人工选择和生成节点分别只有一个 attempt。
- SAM/TRELLIS2 各有一条 worker 记录，均为 exit_observed、exit_code=0。
- 输出 Artifact 与已登记子结果的完整引用闭包通过校验。
- GLB 用 trimesh 重新加载成功，1 个 geometry、178480 个顶点、282527 个三角面。
- QA 核心检查通过；总体 warn 来自 relative scale 与机械估计的 forward。未请求 collision，render-back 未实现，因此二者 skipped；不据此宣称质量验收完成。
- 人工等待阶段关闭 CLI 并启动审查服务后，SAM attempt 仍为 1。
- 完成后正常关闭并重启审查服务，页面与运行查询均返回 HTTP 200，状态仍为 succeeded。恢复前后 node_attempts、node_states 和决定回执完全一致（包含输出引用与 worker 标识），revision 从 33 增至 34，没有重复推理。

仓库外证据：`<DATASET_ROOT>/dag-validation-20260920/` 中 config.json、start.json、completed.json、restored.json、validation.json、review-state.json、review-restored-state.json 和 service/executions 下发布结果。输入、模型、Store 和生成资产不提交仓库。

## 限制

启动时 GPU 存在其他活动，驱动未列出计算进程，因此本次只验证功能，不报告性能 benchmark。尚未对真实模型执行中的 SIGKILL 做验收；CPU 故障注入不替代该项。没有进行几何质量调优。

完成后的服务重启约需 6 分钟才开始监听，启动耗时仍需单独定位；本次没有据此评价交互性能。

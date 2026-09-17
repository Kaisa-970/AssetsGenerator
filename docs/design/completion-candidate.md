# Completion Candidate v1

当前实现是补全准备阶段的独立生成候选，不是已实现的几何约束 completion。
用户显式调用 `build_completion_candidate()`，输入同一 Store 内的 ObservationBundle、
重建 AssetRelease、带 provided mask 的 view_id，以及单图生成 ResolvedPlan。
没有自动触发策略，没有自动替换原资产。

已检查本地 TRELLIS.2 `Trellis2ImageTo3DPipeline.run()`：公开入口接收 image、seed、
sampler 参数和 pipeline_type，不接收重建 mesh 或可见表面保留约束。因此复用既有
image_asset_v2/shape_generation@1，而不将其冒充 completion@1 Backend。
Pipeline OperatorSpec 不变，候选包只是两个既有资产发布的关联层。

## 身份与发布

新增不可变 Artifact kind `completion_candidate`，schema `CompletionCandidate@1.0`。
其 JSON 保存 policy、observations、selected_view_id/image/mask、两份 release 引用、
生成 run_id 与来源标记，以及下列明确状态：

- policy=independent-generation-candidate-v1，status=candidate_only；
- geometry_conditioning=false，alignment=not_performed，fusion=not_performed；
- observed_surface_preservation=not_guaranteed，review_status=pending。

重建 release 必须有效、引用同一 observation identity 且组件为 reconstructed；选择视图
必须存在且有 mask。输出目录原子发布，包含 candidate.json、candidate-ref.json、
reconstructed/（原 asset/release/files 字节）和 generated/（完整单图发布与 BuildRun）。
生成失败不留下半成品候选目录，已有重建资产不变；生成工作流失败记录仍保留在 Store。

候选包不是 AssetRelease，也不是 SceneDefinition。两份资产分别 canonicalize，虽然 frame
标签可能同为 asset_canonical，却没有共同姿态/尺度保证；不得据同名 frame 自动叠放或融合。
整个候选是 generated，不能声称其正面 reconstructed、背面 generated；没有区域证据，
不签发伪造 region map。生成 provenance 只引用真实使用的图像处理链，重建 release 只是
候选关联信息，不是生成模型实际使用的几何输入。

## 后续真正的 completion

另行选择支持几何条件的 Backend 或定义明确对齐/融合 Operator，验证保留区域，记录
SpatialTransform、拓扑变化和 reconstructed/generated 区域证据。首版不承诺闭合、水密、
物理可用或背面真实。质量调优暂缓，但数据完整性、错误分类和来源语义仍必须成立。

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

## 显式候选对齐 v1

`align_completion_candidate()` 实现 `align_candidate@1`（端口定义见 operators-v1.yaml），
将生成 release 的 GLB 显式变换到重建 release 的导出空间。源/目标 frame 使用
`<release artifact_id>/gltf_export`，避免同名 frame 暗示共同坐标系。
矩阵遵循列向量 `p_target = T_target_source p_source`，使用 GLB 的 +Y up / +Z forward，
不是 canonical 的 +Z up。仅接受有限 4×4 仿射矩阵、正统一缩放与 proper rotation；
拒绝镜像、剪切、非统一缩放和错误 frame。矩阵平移以目标单位表示，缩放显式映射
源坐标单位至目标坐标单位；支持 meter/relative_unit，不推断公制标定或配准正确性。

`spatial_transform` 现在也可持久化为 Artifact（SpatialTransform@1.0），作为独立证据。
新增 `candidate_alignment` / `CandidateAlignment@1.0` 保存原 candidate、源/目标 release、
新 GLB、变换及 provenance 引用。新 GLB identity 记录目标 frame/unit 和变换 identity。
provenance 引用 candidate、源/目标 GLB 和 transform，保留 generated 来源，并记录单位映射
与 NumPy/Trimesh 版本。该入口直接执行 Operator 契约校验，不新增 DAG。最小 BuildRun 记录 align_candidate 和
materialize_alignment 两个 NodeAttempt；provenance.run_id 可通过 Store.get_build_run 查询。
矩阵预检先拒绝非法/非有限值，尚不创建运行；预检通过后开始执行即持久化 running 记录，
后续契约/执行错误标记失败；预览与目录发布错误记为
release_failed。成功目录包含最终 run.json，发布异常时 Store 索引更新为 failed。

输出目录原子发布。原 GLB 字节不变；对齐 GLB 保留场景实例变换、纹理、UV、顶点颜色和
各 geometry 的材质。`overlay.glb` 只用于诊断：青色为重建，橙色为生成，包含独立 geometry，
未进行融合。`index.html` 复用现有模型查看器，必须通过 HTTP 打开；查看器默认依赖 CDN，
也可在 viewer-assets/model-viewer.min.js 提供本地副本。

状态始终为 `aligned_candidate_only`、`explicit_transform_applied`、`fusion=not_performed`、
`review_status=pending`。不继承原 release 的 QA 结论，不创建冒充融合结果的 AssetRelease。
发布失败可能保留 Store 中无引用的不可变 Artifact，但不会留下半成品输出目录。

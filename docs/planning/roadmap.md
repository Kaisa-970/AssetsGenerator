# Roadmap and Model Positioning

**版本**：v0.4.1

## 1. 模型在系统中的位置

```text
Hunyuan3D / TRELLIS / SAM 3D Objects
    Object Generation and Completion

VGGT / COLMAP
    Geometry Frontend

SHARP / InfiniSplat / AnySplat
    Gaussian Reconstruction and Novel-view Representation

FIRE3D
    Reality to Structured Assets

GS-Playground
    Structured Assets to Simulation and Robot Learning
```

模型是 Operator 的 Backend，不定义系统本身。

## 2. Phase 1：真实 Vertical Slice

```text
Single RGB + provided mask
    -> RGBA prepare and ObservationBundle
    -> one real Shape Backend
    -> deterministic relative-scale canonicalization
    -> Assemble AssetDefinition
    -> GLTF2 export and AssetRelease
    -> provenance and geometry QA
```

Phase 1 实现：

```text
Blob / Artifact / ExecutionOutput 最小身份
固定 AssetDefinition schema
ArtifactRef / StructuredValue 封闭端口
backend_native -> asset_canonical -> gltf_export frame 链
固定 Pipeline 和精确 kind 检查
本地 Artifact Store
本地同步或进程 Worker adapter
BuildRun
```

Phase 1 不实现：

```text
自动 Router
分布式 Worker
通用 schema migration
kind 继承体系
完整 FrameGraph 引擎
复杂缓存策略
camera registration
```

验收目标：

> 用一个真实模型，从单张图片稳定生成可追溯、可校验、可再次加载的 canonical structured asset。

## 3. Phase 2：完整单图流程

加入 Primary Segmentation Backend，使 mask 成为可选输入；同时补充 Worker 异步状态、staging/commit、错误分类和基础缓存。

当前实现使用固定 revision 的 `ZhengPeng7/BiRefNet_lite` 作为唯一 Primary Segmentation
Backend。Core 通过本地进程 Worker 调用隔离环境，记录异步状态、幂等键、超时、取消和结构化
错误。基础缓存仅覆盖能够由固定模型 revision、参数和输入 Artifact 身份完整描述的分割结果；
TRELLIS Shape Backend 暂不缓存，因为模型名可能指向可变快照，执行前无法获得可信的模型 digest。

当前 build workflow 仍是 Phase 2 的固定 vertical slice：YAML 用于契约编译和端口校验，
节点执行顺序及 Backend 绑定由 Python workflow 明确编排。接入第二个 Shape Backend 前，
必须引入 ResolvedPlan/Backend registry，让配置真正驱动调度。

## 4. Phase 3：Benchmark 基线

在第二个 Shape Backend 之前完成：

```text
BenchmarkDataset v1
统一预处理和 GLTF2 Export Profile
自动 MetricSuite
人工评分表和失败分类
EvaluationRun / ComparisonReport
```

先用第一个 Backend 产生基线。

### 完成条件（最小工程基线）

Phase 3 的目标是建立可复核的单 Backend 工程基线，不是对模型视觉质量做代表性排名。满足以下条件即可收口：

- 固定一批明确来源的输入与 mask，并记录输入、配置和模型身份。
- 使用统一预处理、canonicalization、GLTF2 Export Profile 和 Geometry QA。
- 每个运行样本都有成功、执行失败或人工视觉淘汰的终态记录。
- 保留逐例报告、耗时、基础 QA、发布 digest 和交互式 GLB 评审页面。
- 至少保留两个可复用成功样本作为后续开发回归输入，并保留失败或淘汰样本作为限制证据。

人工评价只记录总体 `keep`、`improve` 或 `reject` 决策及可选备注，不要求主观分项分数。视觉淘汰不等同于执行失败；没有对照实验时不推断具体根因，也不把少量样本推广为模型能力结论。

## 5. Phase 4：Backend 可替换性

接入第二个 Shape Backend，并加入 Model Registry、Router 和 ResolvedPlan。两个 Backend 必须满足相同 OperatorSpec，并在同一 benchmark 上生成可比较报告。

状态：已于 2026-09-16 收口。当前实现完成 Backend Registry、不可变 `ResolvedPlan`、配置驱动绑定、TRELLIS.2 与 TripoSR 两个真实 Shape Backend，以及相同飞机/自行车输入上的串行 GPU 回归。`image_asset_v2.yaml` 使用 `backend: trellis2` 声明默认绑定，CLI `--shape-backend` 可覆盖；多视图 YAML 分别声明两类 Backend。Phase 4 的 Router 范围限于显式 Backend 选择和绑定解析；自动资源调度不属于本阶段关闭条件。自动工程指标和可交互输出已经记录，人工视觉评价仍作为后续评审活动，不构成 Backend 可运行性的阻塞项。

## 6. Phase 5：扩展 QA

加入 CameraRegistration、registration gate 和 render-back QA。注册失败时像素级指标为 skipped 或 warn。

状态：按当前开发顺序显式延期，先进入 Phase 6。Phase 6 不得假设 CameraRegistration 或 render-back QA 已实现；依赖注册质量门槛的功能暂不接入。

## 7. Phase 6：多图与 Hybrid

```text
Multi RGB / RGBD / Video
    -> segmentation
    -> VGGT or geometry frontend
    -> cameras / depth / points
    -> reconstruction
    -> optional generated completion
    -> AssetDefinition
```

同一资产可以包含 reconstructed 和 generated 区域，不能用单个 mode 概括来源。

模型中立执行基线已经闭环，专项约束见 [多视图与 Hybrid 契约](../design/multi-view-hybrid.md)，验证记录见 [Phase 6 多视图 Core 基线报告](../reports/phase6-core-baseline-v1.md)：严格 Observation IR、原子且文件名无关的 manifest 导入、集合 cardinality、通用 Backend 绑定、Backend 执行 metadata provenance，以及 Fake Backend 驱动的多视图端到端 workflow。该基线验证 Core 契约、canonicalization、component provenance、QA、BuildRun 和 release，不代表真实多视图模型可用。首个真实 geometry frontend 已选 DA3-Base，接入约束见 [DA3 契约](../design/da3-backend.md)；DA3 已通过双帧真实 GPU smoke 和 4/8/16 帧串行资源验证，见 [多帧报告](../reports/da3-frame-scaling-v1.md)。这些验证不代表物体几何质量达标；reconstruction 首版选择 [Open3D TSDF](../design/open3d-backend.md)，将 RGB/深度/相机融合为带顶点颜色的网格，已完成 [DA3→Open3D 真实发布 smoke](../reports/open3d-tsdf-smoke-v1.md)；几何约束 completion Backend 等待后续选型；已完成 [独立生成候选准备](../design/completion-candidate.md) 与 [TRELLIS.2 真实关联发布验证](../reports/completion-candidate-smoke-v1.md)，另已实现显式矩阵候选对齐与独立图层叠加预览，见 [对齐验证](../reports/candidate-alignment-smoke-v1.md)；另提供人工变换调整、图层切换、保存和确认/拒绝入口；已扩展跨会话恢复、显式选定、人工方框区域选择及独立组件组合发布；尚无自动配准、拓扑融合或质量验收。

### Phase 6 首版流程里程碑

首版范围：多视图观测 → DA3 → Open3D 重建 → 独立生成候选 → 人工对齐/检查 →
显式选定 → 人工方框区域组合 → 标准 AssetDefinition/AssetRelease 与基础 QA。
此流程已完成，验证见 [标准发布报告](../reports/composition-release-smoke-v1.md)。
此处关闭的是流程首版里程碑，不是原规划完整 Phase 6。

明确延期：几何约束 completion、自动配准、拓扑融合/水密化、代表性物体质量阈值与验收、
视频抽帧策略、RGBD invalid-value/metric-scale policy、通用跨表示 region-map。
Phase 5 CameraRegistration/render-back QA 继续延期；当前不宣称质量或物理可用。

## 8. Phase 7：Scene to Assets

长期借鉴 FIRE3D，引入 Detection、Instance Segmentation、Object Pose、Canonicalization、Completion 和 SceneDefinition。

Phase 7 流程首版已闭环：显式 mask 或 SAM unknown 候选经人工选择后，串行复用 Shape Backend
生成独立资产；再以精确 release 引用和人工 canonical→world 位姿创建独立
AssetInstance/SceneDefinition。已保留父子 BuildRun、mask/selection/instance/scene provenance、
完整嵌套资产包、可视 mask 选择、可视场景布局与原子发布。自动 detection/segmentation、
位姿估计、几何约束 completion 与场景质量验收延期，不以人工输入冒充模型能力。
使用见 [指南](../guides/scene-to-assets.md)。

```text
Scene Observation
    -> N x AssetDefinition
    -> N x AssetInstance
    -> SceneDefinition
```

## 9. Phase 8：Real to Sim

Phase 8 首版已完成三个窄切片：deterministic collision generation；绑定精确 AssetDefinition、
canonical visual Artifact 和 frame 的两点距离 metric calibration；以及绑定 meter-unit 资产和单一
collision Artifact 的用户显式 dynamic rigid-body properties。三者均保留独立 BuildRun、逐输出
provenance、原子发布和真实 CPU smoke，默认图像 Pipeline 保持不变。

系统不自动估计 mass、friction、inertia 或 center of mass。下一切片评估 USD/IsaacUSD 交付边界；
runtime 同步和 articulation 后续再做。

加入：

```text
collision
metric scale calibration
rigid body physics
USD / IsaacUSD export
articulation in later iterations
```

进一步验证 Mesh、Gaussian 和 Physics 状态同步，以及 GS-Playground / Isaac runtime。

## 10. 暂不解决

```text
自动 joint detection
真实 mass / friction estimation
复杂动态对象
完整 scene graph reasoning
工业 CAD reverse engineering
高质量 automatic retopology
```

## 11. 参考项目

- FIRE3D: https://xiahongchi.github.io/Fire3D/
- GS-Playground: https://gsplayground.github.io/
- TRELLIS: https://github.com/microsoft/TRELLIS
- SAM 3D Objects: https://github.com/facebookresearch/sam-3d-objects
- VGGT: https://github.com/facebookresearch/vggt
- Hunyuan3D: https://github.com/hunyuan3d/hunyuan3d

## 当前进展（2026-09-16）

Phase 3 已整理 13 次真实 VOC 实例运行（12 成功、1 失败）与用户选出的 4 个相对完整模型。
[初版报告](../reports/phase3-baseline-v1.md)明确记录采样、分辨率和人工评价局限；
[已筛选输入基线](../reports/phase3-approved-baseline-v1.md)已完成最小工程基线：3 例执行成功，人工保留飞机和自行车、淘汰瓶子；两个成功样本可作为开发回归输入。Phase 4 已完成 Registry、带契约摘要的不可变 ResolvedPlan、Pipeline 默认绑定、BuildRun 绑定记录，以及 TRELLIS.2/TripoSR 的 CLI 注册和显式选择。

第二个 Shape Backend 选择 TripoSR，接入边界见 [TripoSR Backend 契约](../design/triposr-backend.md)。独立环境预检、真实 GPU smoke、native frame 验证和飞机/自行车回归运行已经完成，见 [Phase 4 TripoSR 验证报告](../reports/phase4-triposr-validation-v1.md)。Phase 4 已关闭；人工视觉比较仍可继续补充，但不改变已验证的 Backend 可运行性结论。Phase 5 已显式延期，当前开始 Phase 6 的多图与 Hybrid 基础契约。

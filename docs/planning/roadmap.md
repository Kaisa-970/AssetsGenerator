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

状态：已于 2026-09-16 收口。当前实现完成 Backend Registry、不可变 `ResolvedPlan`、配置驱动绑定、TRELLIS.2 与 TripoSR 两个真实 Shape Backend，以及相同飞机/自行车输入上的串行 GPU 回归。Phase 4 的 Router 范围限于显式 Backend 选择和绑定解析；自动资源调度不属于本阶段关闭条件。自动工程指标和可交互输出已经记录，人工视觉评价仍作为后续评审活动，不构成 Backend 可运行性的阻塞项。

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

## 8. Phase 7：Scene to Assets

借鉴 FIRE3D，引入 Detection、Instance Segmentation、Object Pose、Canonicalization、Completion 和 SceneDefinition。

```text
Scene Observation
    -> N x AssetDefinition
    -> N x AssetInstance
    -> SceneDefinition
```

## 9. Phase 8：Real to Sim

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

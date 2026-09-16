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

## 5. Phase 4：Backend 可替换性

接入第二个 Shape Backend，并加入 Model Registry、Router 和 ResolvedPlan。两个 Backend 必须满足相同 OperatorSpec，并在同一 benchmark 上生成可比较报告。

## 6. Phase 5：扩展 QA

加入 CameraRegistration、registration gate 和 render-back QA。注册失败时像素级指标为 skipped 或 warn。

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
代表性数据选择及统一人工评分仍待完善。暂未进入 Phase 4。

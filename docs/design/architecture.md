# Real-to-Sim Asset Compiler

## 模块化图生 3D / 多图重建资产 Pipeline 设计

**版本**：v0.4.1
**目标阶段**：V1 原型设计
**核心定位**：将现实观测编译为可复用、可追溯、可校验并能逐步进入仿真系统的结构化 3D 资产。

## 1. 目标与边界

系统长期支持：

```text
Single Image
Multi Images
Video
RGBD
3DGS Scan
```

经过：

```text
Understanding
Generation / Reconstruction / Hybrid Completion
Asset Processing
Validation
Export
```

输出：

```text
AssetDefinition
├── Mesh
├── Renderable Material
├── Gaussian / Point Cloud (optional)
├── Local Frame and Scale Status
├── Semantic (optional)
├── Collision / Physics (later phases)
└── Provenance and Quality Reports
```

V1 只解决单主体、单图、静态可渲染资产，不同时建设完整场景理解、物理估计或分布式平台。

## 2. 核心设计决策

### 2.1 Operator 与 Backend 分离

Operator 定义能力及唯一输入输出契约，Backend 实现具体模型。Registry 只记录 Backend 实现的 OperatorSpec、支持的 profile、资源要求和版本信息。

### 2.2 AssetDefinition 与 AssetInstance 分离

可复用资产不保存 `world_pose` 或 `instance_id`。场景中的位置和实例身份保存在 AssetInstance。

### 2.3 Artifact 不可变并保留 provenance

```text
Blob                原始内容身份
Artifact            稳定语义身份
ExecutionOutput     某次执行事件身份
```

URI 只用于访问，不参与内容身份。每次处理产生新 Artifact，不能覆盖上游结果。

### 2.4 请求意图与数据来源分离

```text
intent = generate | reconstruct | hybrid
```

意图用于规划 Pipeline。最终来源记录在具体 Artifact、组件或区域上，例如 `observed`、`reconstructed`、`generated` 或 `mixed`。

### 2.5 空间契约显式化

内部使用右手坐标、`+Z` up、`+X` forward。所有空间 Artifact 声明 frame 和 unit，Backend 必须输出 native frame 描述，Export 必须执行显式轴和单位转换。

## 3. 总体架构

```text
Reality Observation
        ↓
ObservationBundle
        ↓
Typed Pipeline
        ↓
Operator + Backend
        ↓
Immutable Artifact Store
        ↓
AssetDefinition + QualityReport
        ↓
AssetRelease
        ├── Asset Library
        └── Simulator (later phases)
```

详细规范：

- [Asset IR](asset-ir.md)
- [Coordinate System](coordinate-system.md)
- [Pipeline and Backend Contract](pipeline-contract.md)
- [Artifact Store](artifact-store.md)
- [Validation and Evaluation](evaluation.md)
- [Roadmap](../planning/roadmap.md)

## 4. V1 Pipeline

```text
Single RGB + Provided Mask
    ↓
RGBA Prepare + ObservationBundle
    ↓
One Real Shape Generation Backend
    ↓
BackendNativeFrame
    ↓
Deterministic Relative-scale Canonicalization
    ↓
Assemble AssetDefinition
    ↓
GLTF2 Export + AssetRelease
    ↓
Provenance + Geometry QA
```

V1 mandatory：

```text
Visual Mesh
Renderable Material
Deterministic Local Frame
Bounding Box
Provenance
Build Metadata
Geometry QA
GLTF2 Export
```

V1 optional：

```text
Semantic Label
Full PBR Channels
Gaussian
Collision Mesh
Metric Scale
Render-back QA
```

## 5. Phase 1 实现限制

Phase 1 只实现：

```text
固定 schema 版本
封闭 PortValue kind 和精确匹配
固定 RGB + Mask Pipeline
一个真实 Backend adapter
本地 Artifact Store
本地同步或进程执行
backend_native -> asset_canonical -> gltf_export frame 链
```

Phase 1 的真实 Shape Backend 使用本地独立进程适配器。Core 通过临时 JSON 请求和本地
Artifact 路径与 Backend 进程通信，并设置有限超时；这不是远程 Worker 服务协议。远程作业、
取消、健康检查和统一部署接口仍属于后续阶段。

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

FakeBackend 只用于契约测试，不能替代真实模型的端到端验证。

## 6. V1 验收标准

```text
固定 Pipeline 通过静态端口检查
真实 Backend 成功生成非空 Mesh
Backend native frame 被显式记录
canonical GLB 可由独立 loader 再次加载
Renderable Material 正确显示
空间 Artifact 的 frame 和 unit 完整
Blob / Artifact digest 校验通过
mandatory provenance 完整
Geometry QualityReport overall_status != fail
AssetRelease 可从 asset.json 重新物化
```

Render-back 在 Phase 1 允许 `applicable=false, status=skipped`。Collision 未请求时，其检查同样必须 skipped，不能导致 Validation 失败。

## 7. 当前实施顺序

```text
1. 用一个真实 Shape Backend 跑通 V1 vertical slice
2. 固化 BenchmarkDataset 和第一个 Backend 基线
3. 引入 Registry 和最小 ResolvedPlan，去除 workflow 的固定 Backend 绑定
4. 接入第二个 Shape Backend，并加入 Router 和比较报告
5. 扩展 render-back、多图、Scene-to-Asset 和 Real-to-Sim
```

当前最重要的验收目标是：

> 给定一张 RGB 和一个 Mask，通过一个真实 Shape Backend，稳定生成具有 canonical coordinate、明确 provenance、可重新加载并通过基础 QA 的 AssetDefinition 和 GLB AssetRelease。

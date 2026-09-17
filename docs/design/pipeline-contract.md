# Pipeline and Backend Contract

**版本**：v0.4.1

## 1. PortValue

Operator 端口支持 Artifact 和小型结构化值：

```python
@dataclass(frozen=True)
class StructuredValue:
    kind: str
    schema_name: str
    schema_version: str
    value: dict
    provenance_id: str | None

PortValue = ArtifactRef | StructuredValue
```

大型二进制数据使用 `ArtifactRef`。CameraRecord、SpatialTransform、ImageWarp、BackendNativeFrame、QualityReport、AssetDefinition 等使用 `StructuredValue`。

V1 使用封闭 kind 集合和精确匹配，不实现子类型继承。类型转换通过显式 Operator 完成。

结构化 kind：

```text
camera_record
spatial_transform
image_warp
backend_native_frame
camera_registration
pbr_material
observation_bundle
asset_definition
asset_release
asset_spatial_info
frame
semantic_info
quality_report
export_profile
segmentation_result
component_provenance
```

`spatial_transform` 是带 source/target frame 的 4x4 空间变换。`image_warp` 描述 crop、resize、padding 和像素坐标映射，二者不能混用。

```text
spatial_transform schema: source_frame_id + target_frame_id + matrix4x4
image_warp schema: source_view_id + target_view_id + crop + resize + padding + pixel_transform3x3
```

## 2. OperatorSpec

Operator 是 WHAT 的唯一契约来源：

```python
class Operator:
    def specification(self) -> OperatorSpec:
        ...

    def execute(
        self,
        inputs: dict[str, PortValue | list[PortValue]],
        context: RunContext,
    ) -> dict[str, PortValue | list[PortValue]]:
        ...
```

```yaml
name: shape_generation
version: 1
inputs:
  image:
    kinds: [rgba_image]
    cardinality: one
  mask:
    kinds: [binary_mask]
    cardinality: zero_or_one
outputs:
  mesh:
    kinds: [triangle_mesh]
    cardinality: one
    frame:
      equals_port: native_frame.frame_id
    unit:
      equals_port: native_frame.unit
  material:
    kinds: [pbr_material]
    cardinality: one
  native_frame:
    kinds: [backend_native_frame]
    cardinality: one
  camera_hint:
    kinds: [camera_record]
    cardinality: zero_or_one
```

Pipeline 编译时检查：

```text
端口存在
载体和 kind 精确匹配
cardinality 满足
schema version 匹配
空间 Artifact 具备 frame 和 unit
端口声明的 frame / unit 前置条件满足
不存在环或缺失输入
条件节点与可选引用可解析
```

运行时每个 `ArtifactRef` 端口在读取 manifest 契约前必须通过 Blob digest 校验；损坏或缺失的内容不能进入 Operator，也不能作为 Backend 输出被接受。

## 3. Backend 与 Registry

Backend 是 HOW，只实现某个 OperatorSpec。Registry 不重复 Operator 的基本端口定义。

```yaml
backends:
  trellis:
    operator: shape_generation@1
    backend_version: 1.0.0
    model_digest: sha256:...

    supported_profiles:
      - profile: rgba_to_mesh
        gaussian_output: true

    resources:
      platform: linux
      gpu_vendor: nvidia
      min_vram_gb: 16

    determinism: seeded
```

Registry 只记录：

```text
backend name and version
implemented OperatorSpec version
supported profiles and optional capabilities
model/container digest
resource and platform requirements
determinism
availability and health information
```

Pipeline 节点可通过 `backend` 声明默认实现。运行请求可覆盖该名称；Core 使用 Registry 将名称解析为实现，生成不可变的 `ResolvedPlan` 后再开始执行。`ResolvedPlan` 绑定 pipeline name/version、实际 Pipeline 与引用 OperatorSpec 的内容摘要、node、Backend name/Registry 声明版本和进程内实现。BuildRun 持久化契约摘要以及 node 到 Backend name/Registry 声明版本的最终绑定，模型内容身份仍由该节点的 provenance 记录。Registry 不复制 OperatorSpec 的端口定义。

Backend 适配器负责把模型原生输入输出转换成 OperatorSpec，包括输出 `BackendNativeFrame`。不能让无坐标语义的裸 pose 或 mesh 进入 Pipeline。

## 4. CanonicalizationOperator 契约

```yaml
name: canonicalize
version: 1
inputs:
  mesh:
    kinds: [triangle_mesh]
    cardinality: one
    frame:
      equals_port: native_frame.frame_id
    unit:
      equals_port: native_frame.unit
  native_frame:
    kinds: [backend_native_frame]
    cardinality: one
outputs:
  mesh:
    kinds: [triangle_mesh]
    frame: asset_canonical
    cardinality: one
  canonical_frame:
    kinds: [frame]
    cardinality: one
  transform:
    kinds: [spatial_transform]
    source_frame: input.native_frame.frame_id
    target_frame: output.canonical_frame.frame_id
    cardinality: one
  spatial_info:
    kinds: [asset_spatial_info]
    cardinality: one
```

CanonicalizationOperator 创建 `asset_canonical` Frame，并发布 `T_asset_canonical_backend_native`。它不能只返回一个没有 source/target frame 的矩阵。

## 5. V1 Pipeline

```yaml
pipeline: image_asset_v1
version: 1

inputs:
  source_image:
    kind: rgb_image
  source_mask:
    kind: binary_mask
  semantics:
    kind: semantic_info
    cardinality: zero_or_one
  export_profile:
    kind: export_profile

nodes:
  prepare_observation:
    operator: prepare_observation
    inputs:
      image: pipeline.inputs.source_image
      mask: pipeline.inputs.source_mask
    outputs:
      bundle:
        kind: observation_bundle
        persist: true
      rgba: object_rgba
      image_warp: source_to_backend_image_warp

  generate_shape:
    operator: shape_generation
    inputs:
      image: prepare_observation.outputs.rgba
      mask: pipeline.inputs.source_mask
    outputs:
      mesh: raw_mesh
      material: renderable_material
      native_frame: backend_native_frame
      camera_hint: backend_camera_hint

  canonicalize:
    operator: canonicalize
    inputs:
      mesh: generate_shape.outputs.mesh
      native_frame: generate_shape.outputs.native_frame
    outputs:
      mesh: canonical_mesh
      canonical_frame: asset_canonical_frame
      transform: native_to_canonical
      spatial_info: asset_spatial_info

  register_camera:
    operator: camera_registration
    when: request.qa contains render_back
    inputs:
      observation: prepare_observation.outputs.bundle
      image_warp: prepare_observation.outputs.image_warp
      mesh: canonicalize.outputs.mesh
      canonical_transform: canonicalize.outputs.transform
      backend_camera_hint: generate_shape.outputs.camera_hint?
    outputs:
      camera: registered_camera
      registration: camera_registration

  collision:
    operator: collision_generation
    when: request.outputs contains collision
    inputs:
      mesh: canonicalize.outputs.mesh
    outputs:
      collision: collision_mesh

  validate:
    operator: validation
    inputs:
      mesh: canonicalize.outputs.mesh
      material: generate_shape.outputs.material
      collision: collision.outputs.collision?
      observation: prepare_observation.outputs.bundle
      camera: register_camera.outputs.camera?
      registration: register_camera.outputs.registration?
    outputs:
      report:
        kind: quality_report
        persist: true

  assemble_asset:
    operator: assemble_asset
    inputs:
      mesh: canonicalize.outputs.mesh
      material: generate_shape.outputs.material
      collision: collision.outputs.collision?
      spatial: canonicalize.outputs.spatial_info
      quality: validate.outputs.report
      observations: prepare_observation.outputs.bundle
      semantics: pipeline.inputs.semantics?
    outputs:
      asset:
        kind: asset_definition
        persist: true

  export:
    operator: export
    inputs:
      asset: assemble_asset.outputs.asset
      profile: pipeline.inputs.export_profile
    outputs:
      glb:
        kind: gltf_asset
        persist: true
      release:
        kind: asset_release
        persist: true
```

Phase 1 要求 `source_mask`。Phase 2 的 `image_asset_v2` 将其改为可选输入，并通过固定的
`resolve_mask` / `segmentation@1` 节点统一产生 binary mask：提供 mask 时验证并复用，缺失时调用
Primary Segmentation Backend。`PrepareObservationOperator` 负责验证 image/mask 尺寸、生成 RGBA
和 ImageWarp，并创建供 BuildRun 与 AssetDefinition 引用的 ObservationBundle。

`AssembleAssetOperator` 只组装 AssetDefinition，不执行格式导出。若没有 semantic 输入，它写入 `semantic_class = null, source = unknown`，不调用额外识别模型。geometry、material、spatial、observation 和 quality 引用各自已有 provenance；组装操作本身另有一条 provenance 记录。

ExportOperator 读取已组装的 AssetDefinition、canonical mesh、PBRMaterial 和 GLTF2Profile，通过显式 `appearance_mode` 选择外观来源。单图流程使用 `preserve_mesh`，保留 mesh 内嵌纹理、UV、顶点颜色及各 geometry 的材质绑定；从 GLB 提取的颜色摘要不覆盖原有外观。多视图流程使用 `apply_material`，将独立 PBRMaterial 的因子及可选纹理应用到每个 geometry；纹理必须可解码，存在纹理时 mesh 必须提供逐顶点 UV。导出产生带 `gltf_export` frame 的 GLB Artifact，以及引用 AssetDefinition、GLB、纹理和报告的 AssetRelease manifest。外观模式记录在 export profile、GLB identity metadata 和导出 provenance 中。

`when` 在规划阶段解析，`?` 表示引用可能缺失，只能绑定最小基数为 0 的端口；`one` 和 `one_or_more` 在编译期拒绝可选引用。可选输入缺失时，Operator 根据自己的检查适用性处理，不能把缺失自动当成运行失败。

Phase 6 的集合端口使用 `one_or_more` 或 `zero_or_more`，运行时值必须是 list。Pipeline 编译器不执行标量与集合之间的隐式包装或展开。多视图 Backend 契约和 Fake Backend 执行基线见 [多视图与 Hybrid 契约](multi-view-hybrid.md)。

例如 collision 分支关闭时：

```json
{
  "check_id": "collision_loadable",
  "applicable": false,
  "status": "skipped",
  "reason": "collision output was not requested"
}
```

## 6. StructuredValue 持久化

OperatorSpec 输出可声明：

```yaml
asset:
  kind: asset_definition
  persist: true
```

Runtime 在节点成功后调用 `ArtifactStore.persist_structured(...)`，将规范化后的 StructuredValue 变成 ArtifactRef。需要进入 AssetRelease、跨 Run 引用或作为证据的结构化值必须持久化。

持久化不改变 kind。例如 `StructuredValue<quality_report>` 经持久化后成为 `ArtifactRef<quality_report>`；下游仍按 `quality_report` 匹配，只是载体从内联值变为引用。OperatorSpec 可以声明接受 `structured`、`artifact_ref` 或两者。

## 7. BuildRun

```python
@dataclass
class BuildRun:
    run_id: str
    pipeline_name: str
    pipeline_version: str
    status: str
    inputs: dict[str, ArtifactRef | StructuredValue]
    node_attempts: list[NodeAttempt]
    started_at: str
    finished_at: str | None
    resolved_backends: dict[str, str] = {}  # BuildRun 1.0 向后兼容可选字段
    resolved_plan_contract_digest: str | None = None
    resolved_backend_versions: dict[str, str] = {}

@dataclass
class NodeAttempt:
    node_id: str
    attempt: int
    operator: str
    backend: str | None
    status: str
    execution_mode: str  # executed | cache_hit
    started_at: str
    finished_at: str | None
    error_code: str | None
    outputs: dict[str, ArtifactRef | StructuredValue]
```

`resolved_plan_contract_digest` 固定本次运行所校验的 Pipeline 与 OperatorSpec 契约，`resolved_backend_versions` 按节点记录 Registry 声明的 binding 版本。它们与 `resolved_backends` 一起构成 `ResolvedPlan` 可持久化部分的审计身份；Backend 返回的模型 revision、dirty 状态、权重 digest 和运行参数仍记录在 ProvenanceRecord 中。为兼容既有 BuildRun，缺失这些字段表示旧记录未捕获相应身份，不能据此推断当前契约或实现版本。

该 schema 只用于复盘一次 Pipeline 实际发生的事情，不承担通用调度系统职责。

## 8. Router 与 ResolvedPlan

Phase 4 先引入最小 Registry 和不可变 `ResolvedPlan`，固定 pipeline version、Pipeline/OperatorSpec 内容摘要、node 及 Backend name/version。第二个 Backend 接入后，再由 Router 根据资源和策略生成计划，并补充资源选择、fallback policy 与 plan revision。

执行失败时：

```text
retryable error        retry same resolved node
input error            fail run
backend unavailable    create revised plan if policy allows
```

替换 Backend 必须生成新的 plan revision，并重新执行受影响的下游节点。

## 9. Worker API

Phase 1 使用本地同步或进程适配器。后续远程 Worker 使用相同的 `PortValue` 请求和响应语义：

当前 TRELLIS.2 和 BiRefNet_lite 适配器是本地过渡实现：Core 使用独立 Python 环境启动子进程，通过临时 JSON
文件和本地 Artifact 路径交换数据，并设置执行超时。它不等同于以下远程 Worker API。

```http
POST /v1/jobs
GET /v1/jobs/{job_id}
POST /v1/jobs/{job_id}/cancel
GET /v1/capabilities
GET /health
```

状态：

```text
queued
running
succeeded
failed
cancelled
```

Worker 必须支持幂等键、结构化错误、取消和 staging/commit。失败或取消任务不能发布半成品 Artifact。

## 10. Phase 1 实现范围

Phase 1 必须实现：

```text
封闭 PortValue kind
精确端口检查
固定 Pipeline
RGB + provided mask 输入
PrepareObservationOperator
一个真实 Shape Backend adapter
BackendNativeFrame 输出
CanonicalizationOperator
AssembleAssetOperator
GLTF2 ExportOperator
本地进程执行
```

Phase 1 不实现：

```text
自动 Router
动态 DAG 改写
分布式调度
通用 kind 继承
复杂 fallback
```

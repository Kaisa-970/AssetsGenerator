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

ExportOperator 只读取已组装的 AssetDefinition，产生带 `gltf_export` frame 的 GLB Artifact，以及引用 AssetDefinition、GLB、纹理和报告的 AssetRelease manifest。

`when` 在规划阶段解析，`?` 表示可选输入。可选输入缺失时，Operator 根据自己的检查适用性处理，不能把缺失自动当成运行失败。

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

该 schema 只用于复盘一次 Pipeline 实际发生的事情，不承担通用调度系统职责。

## 8. Router 与 ResolvedPlan

Router 在接入第二个 Backend 后引入。输出不可变 `ResolvedPlan`，固定 Backend、版本、资源、端口绑定和 fallback policy。

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

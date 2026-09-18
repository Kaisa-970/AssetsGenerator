# Asset IR Specification

**版本**：v0.4.1

本文定义 Real-to-Sim Asset Compiler 的核心领域对象、Artifact 身份和 V1 资产输出契约。

## 1. 领域对象

系统区分以下对象：

```text
ObservationBundle
    原始 RGB、Mask、Depth、Camera 等输入观测

BuildRun
    一次 Pipeline 编译和执行记录

AssetDefinition
    可复用、可版本化的 canonical asset

AssetInstance
    AssetDefinition 在场景中的一次实例化

SceneDefinition
    AssetInstance、环境和对象关系的集合
```

`world_pose` 和 `instance_id` 属于 `AssetInstance`，不属于可复用的 `AssetDefinition`。

## 2. Blob、Artifact 和执行身份

系统使用三层身份：

```text
Blob
    原始不可变字节

Artifact
    对 Blob 的稳定语义解释

ExecutionOutput
    某次节点执行产生或复用 Artifact 的事件
```

### 2.1 Blob identity 与 location

```python
@dataclass(frozen=True)
class BlobIdentity:
    digest: str
    byte_size: int

@dataclass(frozen=True)
class BlobLocation:
    digest: str
    uri: str
```

```text
blob digest = hash(raw bytes)
```

`BlobLocation.uri` 只是访问提示，不参与 Blob 或 Artifact identity，也不是唯一地址。Artifact Store 通过 `resolve(digest)` 返回当前可用位置；文件从本地目录迁移到 NAS 或对象存储不会改变身份。

### 2.2 Artifact identity

```python
@dataclass(frozen=True)
class ArtifactIdentity:
    kind: str
    schema_name: str
    schema_version: str
    blob_digest: str
    identity_metadata: dict

@dataclass(frozen=True)
class ArtifactAnnotations:
    labels: dict
    created_at: str | None
    debug: dict

@dataclass(frozen=True)
class ArtifactManifest:
    artifact_id: str
    identity: ArtifactIdentity
    annotations: ArtifactAnnotations

@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
```

```text
artifact_id = hash(canonical ArtifactIdentity)
```

只有改变 Artifact 语义的稳定字段进入 `identity_metadata`，例如：

```text
media type and encoding
frame_id
unit
channel layout
triangle winding
normal convention
Gaussian SH degree
texture color space
```

以下字段属于 annotations，不参与 identity：

```text
created_at
hostname
UI label
debug information
temporary path
storage location
```

同一 Blob 在不同 frame、unit 或 encoding 语义下形成不同 Artifact。同一 Artifact 可以位于多个存储位置。

### 2.3 ExecutionOutput

```python
@dataclass(frozen=True)
class ExecutionOutput:
    output_id: str
    run_id: str
    node_id: str
    attempt: int
    port_name: str
    artifact_id: str
    provenance_id: str
    cache_status: str  # executed | cache_hit
```

```text
output_id = unique(run_id, node_id, attempt, port_name[, element_id])
```

两次 Run 即使产生相同字节和相同 Artifact，也拥有不同的 `ExecutionOutput` 和 provenance 记录。

## 3. Provenance

```python
@dataclass
class ProvenanceRecord:
    provenance_id: str
    output_id: str
    output_artifact_id: str
    derived_from_artifact_ids: list[str]

    operator: str
    operator_version: str
    backend: str
    backend_version: str
    model_digest: str | None
    container_digest: str | None

    parameters: dict
    seed: int | None
    run_id: str
    node_id: str
    attempt: int

    source: str  # provided | estimated
    confidence: Confidence | None
    score: float | None
    score_method: str | None
```

`derived_from_artifact_ids` 描述数据派生关系，`output_id` 锚定具体执行事件。

来源枚举：

```text
observed
reconstructed
generated
estimated
derived
mixed
user
database
unknown
```

当一个 Artifact 内不同组件或区域来源不同时，Artifact 可以引用 component/region provenance map。例如可见表面为 `reconstructed`，遮挡面补全为 `generated`。V1 若 Backend 无法提供可靠区域映射，只记录 Artifact 级 `mixed`，不伪造逐面标签。

`confidence` 只用于有校准方法的可靠度：

```python
@dataclass
class Confidence:
    value: float
    method: str
    method_version: str
    calibration_domain: str | None
    evidence_ids: list[str]
```

未经校准的模型输出称为 `score`。不能说明方法和证据时，只保存 `source` 和定性状态。

## 4. ObservationBundle

```python
@dataclass
class ObservationBundle:
    observation_id: str
    views: list[ObservationView]

@dataclass
class ObservationView:
    view_id: str
    image: ArtifactRef
    mask: ArtifactRef | None
    depth: ArtifactRef | None
    camera: CameraRecord | None
```

图像、Mask、Depth 和 Camera 通过 `view_id` 对齐，不能依赖列表下标隐式对应。

最小 CameraRecord：

```python
@dataclass(frozen=True)
class CameraRecord:
    camera_id: str
    image_view_id: str
    model: str  # pinhole | opencv
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    distortion: list[float]
    camera_frame_id: str
    T_world_camera: SpatialTransform | None
    source: str
    confidence: Confidence | None
    score: float | None
    score_method: str | None
```

Phase 1 不要求提供 CameraRecord。导入的相机使用 `source=provided`，geometry frontend 输出使用 `source=estimated`；Observation 中未知外参时可令 `T_world_camera = None`，但进入 Phase 6 reconstruction 的估计相机必须提供到共同 world frame 的外参。Core 将实际传入 reconstruction 的有序相机列表持久化为 `CameraCollection` Artifact，使内外参和顺序进入 Artifact 级 provenance。`registered` 保留给 Phase 5 CameraRegistration 契约，Phase 5 延期期间不得使用。

## 5. AssetDefinition

```python
@dataclass
class AssetDefinition:
    asset_id: str
    asset_version: str
    name: str | None

    geometry: GeometrySet
    appearance: AppearanceSet
    spatial: AssetSpatialInfo
    semantics: SemanticInfo
    physics: PhysicsInfo | None

    source_observation_ids: list[str]
    quality_report_ids: list[str]
    component_provenance: list[ComponentProvenance]
```

`AssetSpatialInfo` 的最小字段在 [coordinate-system.md](coordinate-system.md) 中定义。

Phase 6 的 `component_provenance` 为向后兼容的可选列表，记录 reconstructed、generated 或 mixed 组件及可选 region map；详细约束见 [多视图与 Hybrid 契约](multi-view-hybrid.md)。

`PBRMaterial` 可以引用 base-color、normal、metallic-roughness 和 emissive texture Artifact。
Core 必须验证这些引用的 digest 与 kind，AssetRelease 必须物化所有被最终 AssetDefinition
引用的纹理。canonical GLB 仍是 `GLTF2Profile` 的几何与材质交付文件。

```python
@dataclass
class GeometrySet:
    visual_meshes: list[ArtifactRef]
    gaussians: list[ArtifactRef]
    point_clouds: list[ArtifactRef]
    collision_meshes: list[ArtifactRef]

@dataclass
class AppearanceSet:
    materials: list[PBRMaterial]

@dataclass
class PBRMaterial:
    base_color_factor: list[float]
    base_color_texture: ArtifactRef | None
    normal_texture: ArtifactRef | None
    metallic_roughness_texture: ArtifactRef | None
    emissive_texture: ArtifactRef | None
    alpha_mode: str
```

Phase 8 `PhysicsInfo@1.0` currently represents only a user-supplied dynamic rigid body. It stores
SI mass, canonical-frame center of mass, a canonical-axis inertia tensor about that center of mass,
contact coefficients, the exact collision mesh, and the `RigidBodyProperties` evidence Artifact.
Validation and deferred fields are defined in [Explicit Rigid Body Properties v1](rigid-body-properties.md).

V1 Artifact kind 使用封闭枚举：

```text
rgb_image
rgba_image
binary_mask
depth_map
texture_2d
normal_map
metallic_roughness_map
triangle_mesh
collision_mesh
gltf_asset
point_cloud
gaussian_splat
quality_evidence
zip_bundle
```

`PBRMaterial` 是结构化材质值，可以只包含颜色因子，也可以引用纹理 Artifact。ExportOperator 在 `apply_material` 模式下把该值实际应用到交付 GLB；引用纹理时纹理必须可解码，mesh 必须提供逐顶点 UV。`preserve_mesh` 模式保留 GLB 自带的纹理、顶点颜色及多材质绑定，结构化颜色摘要不替代 mesh 外观；当前单图流程使用此模式。V1 不强制完整 BaseColor、Normal、Roughness、Metallic 通道。

`SemanticInfo` 允许未知值：

```python
@dataclass
class SemanticInfo:
    semantic_class: str | None
    source: str  # user | detector | vlm | backend | unknown
```

## 6. AssetRelease

资产发布包不是单个 Artifact，也不使用 `asset_package` kind。它是由 `asset.json` 定义的一组 ArtifactRef 的发布视图：

```python
@dataclass
class AssetRelease:
    asset_definition: ArtifactRef
    files: dict[str, ArtifactRef]
    export_profile: str
```

`AssetRelease` 没有独立的资产语义身份，它只描述某个 `AssetDefinition` 如何物化和交付。Release manifest 可以持久化为 Artifact 以获得 digest 和跨 Run 引用能力，但 AssetDefinition 仍是资产语义的权威来源。

```text
asset.json                  权威清单
geometry/visual.glb         GLTF2 导出结果
materials/                  外部纹理（如有）
qa/quality-report.json      质量报告
provenance/                 Provenance 记录
run.json                    BuildRun 摘要
```

目录结构只是 `AssetRelease` 的物化形式。需要单文件下载时可以额外生成 `zip_bundle` Artifact，但 ZIP 不取代 `AssetDefinition` 的身份。

## 7. Schema 策略

V1 使用固定 schema 版本和精确版本匹配，不实现通用 migration framework。后续版本遵循以下规则：

```text
新增可选字段          可以保持主版本
删除字段              提升主版本
改变字段含义或单位    提升主版本
跨主版本读取          使用显式 migration
```

## 8. V1 输出契约

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

V1 不要求为了补全 Semantic 或完整 PBR 而引入额外模型。

## 9. StructuredValue 持久化边界

`StructuredValue` 用于 Run 内的小型结构化数据，默认没有独立 Artifact identity。满足以下任一条件时必须持久化：

```text
进入 AssetRelease
跨 Run 引用
作为 provenance 或 QA evidence
需要独立版本和 digest
需要从 run.json 之外恢复
```

Runtime 使用 `ArtifactStore.persist_structured(value)` 将其规范化序列化，并创建 Blob、ArtifactManifest 和 ArtifactRef。Pipeline 端口可用 `persist: true` 声明该边界。持久化后的下游端口接收 `ArtifactRef`，原 StructuredValue 仍可保留在当前 Run 的节点记录中。

V1 至少持久化：

```text
QualityReport
AssetDefinition
GLTF2 export
```

## Completion 候选关联

`completion_candidate` / `CompletionCandidate@1.0` 是不可变的候选关联 Artifact，
保存重建与独立生成 release 引用，不表示已融合 AssetDefinition。字段和发布边界见
[候选契约](completion-candidate.md)。

`candidate_alignment` / `CandidateAlignment@1.0` 保存显式对齐候选的输入、变换、输出及
provenance 引用；`spatial_transform` / `SpatialTransform@1.0` 可作为独立 Artifact 证据。
二者均不代表融合 AssetRelease。详见[显式候选对齐](completion-candidate.md#显式候选对齐-v1)。

`alignment_review` / `AlignmentReview@1.0` 是独立的人工作业决定 Artifact，引用精确
candidate/alignment/transform，追加保留确认和拒绝记录，不修改原对齐包。

`alignment_selection`、`region_selection`、`component_composition` 分别记录采用的检查决定、
精确区域与原始面编号、未融合的组件组合包。三者为不可变关联/证据 Artifact，
详细字段与边界见 [候选契约](completion-candidate.md#恢复选定与区域组合-v1)。

## 显式场景首版

新增 scene_request、asset_instance、scene_definition、scene_layout_review 与
scene_extraction_request、scene_extraction Artifact。实例引用精确 AssetDefinition/AssetRelease，
用户位姿只进入实例，不修改资产定义。scene_layout_review 保存人工发布布局的检查人、完整
布局摘要、精确 SceneDefinition 和 BuildRun 关联，不替代 scene_request 或 scene_definition。
详见 [场景到资产契约](scene-to-assets.md)。

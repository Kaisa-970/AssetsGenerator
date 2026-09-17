# Multi-view and Hybrid Contract

**版本**：v0.1
**阶段**：Phase 6

## 1. 范围

Phase 6 将输入从单张 RGB 扩展为显式多视图观测，并为重建与生成式补全共存建立来源契约。Core 基线不绑定具体模型；首个真实 geometry frontend 已接入 DA3-Base，见 [DA3 契约](da3-backend.md)，reconstruction 选择 [Open3D TSDF](open3d-backend.md)，completion Backend 尚未选定，也不包含 Phase 5 的 CameraRegistration 和 render-back gate。

当前最小数据流：

```text
Multi RGB / RGBD / pre-extracted video frames
    -> import-observations -> persisted ObservationBundle
    -> geometry_frontend@1
    -> cameras + optional depths + points
    -> reconstruction@1
    -> mesh + material + native frame + component provenance
    -> canonicalize mesh and component references / validate / assemble / export
```

`pipelines/multi_view_asset_v1.yaml` 已有显式 Python workflow 和 Fake Backend 端到端测试，可验证 Core 的 Backend 绑定、端口检查、canonicalization、QA、provenance、BuildRun 和 release 集成。Fake Backend 不代表真实 geometry frontend 或 reconstruction 可用；真实实现固定 revision 并完成 GPU 验证前，不宣称 Phase 6 Backend 可生成可用资产。

## 2. ObservationBundle

每个 view 使用显式 `view_id` 对齐 image、mask、depth 和 camera。列表下标不是跨 Artifact 关联键。

必须满足：

- bundle 至少包含一个 view，`view_id` 非空且唯一；
- image kind 为 `rgb_image`；mask kind 为 `binary_mask`；depth kind 为 `depth_map`；
- mask、depth 和 CameraRecord 尺寸必须与对应 image 一致；
- image 必须解码为 RGB；mask 必须是包含前景的单通道 0/255 raster；depth 必须是单通道数值 raster；
- depth Artifact 必须声明 `frame_id` 和 `unit`，并可记录 `invalid_value`；
- CameraRecord 的 `image_view_id` 必须等于所属 view；
- 导入 camera 的 `source` 必须为 `provided`，geometry frontend 输出必须为 `estimated`；Phase 5 延期期间不接受 `registered`；
- 相机内参必须有限，`fx`、`fy` 为正；
- `camera_id` 在 bundle 内唯一；
- `T_world_camera` 若存在，必须为有限的 4x4 齐次刚体变换，source frame 等于 `camera_frame_id`，所有已知外参共享一个 world frame。

Observation identity 是按有序 view 内容计算的稳定摘要。导入的 raster schema 和 media type 来自解码格式，不来自文件名后缀，因此相同字节改名不会改变 Artifact 或 Observation identity。显式 view 顺序属于输入语义；调用方若需要与文件枚举顺序无关，必须在 manifest 中给出稳定顺序。

CLI `import-observations` 接受 JSON manifest，将输入图像导入 Artifact Store 并持久化一个 `ObservationBundle`。RGB 必须是三通道 raster；mask 必须是包含前景的单通道 0/255 raster；depth 当前必须是 Pillow 可读的单通道整数或浮点 raster。Video 在本阶段仅接受调用方已经抽取的帧，不定义自动抽帧或关键帧选择。

`multi_view_asset_v1` 直接接收持久化 `ObservationBundle`。它不接收彼此独立的 image、mask、depth、camera 列表，因为稀疏可选数据无法靠列表位置建立无歧义关联。

## 3. Collection Cardinality

端口 cardinality 使用：

```text
one
zero_or_one
one_or_more
zero_or_more
```

`many` 仅为旧配置的 `zero_or_more` 兼容别名，没有独立语义；新配置统一使用 `zero_or_more`。集合端口必须接收 list，单值端口拒绝 list；Pipeline 编译时同样拒绝单值和集合端口之间的隐式包装或展开。任何转换必须由显式 Operator 完成。

## 4. Backend Contracts

`geometry_frontend@1`：

```text
input:  persisted ObservationBundle
output: one_or_more CameraRecord
        zero_or_more depth_map with frame, unit and view_id
        one point_cloud with frame and unit
```

`reconstruction@1`：

```text
input:  ObservationBundle + cameras + optional depths + points
output: triangle_mesh + PBRMaterial + BackendNativeFrame
        one_or_more ComponentProvenance
```

`BackendNativeFrame` 只接受 `meter` 或 `relative_unit`；未知 unit、非法 axis、handedness 或 forward status 必须在 Backend 节点输出校验中失败，不能在 canonicalization 时静默降级。

Registry 和 `ResolvedPlan` 可以为任意声明 `backend` 的节点解析实现，不再对 `generate_shape` 节点名做特殊限制。OperatorSpec 仍是端口契约的唯一来源。

Geometry frontend 输出的 camera ID 和对应 view ID 必须各自唯一，且 `image_view_id` 必须属于输入 bundle。进入 reconstruction 的每个相机都必须提供齐次刚体 `T_world_camera`，并指向同一个 world frame；point cloud 的 `frame_id` 必须等于该 world frame。每个输出 depth Artifact 的 identity metadata 必须包含唯一的 `view_id`，指向输入 bundle 中的 view，其 `frame_id` 必须等于对应 `CameraRecord.camera_frame_id`，内容必须是单通道数值 raster。depth 与 point cloud 的 unit 必须精确一致，reconstruction 输出 mesh/native frame 的 unit 也必须保持一致；需要换 frame 或换 unit 时必须增加显式 SpatialTransform/转换 Operator。

Backend 的 `backend_metadata` 使用模型中立的可追溯子契约：可包含 `backend_version`、`model_digest`、`container_digest`、`parameters` 和 `backend_source`。`parameters` 只记录影响推理结果的参数；`backend_source` 至少包含非空 `revision`、非空 `source_digest` 和布尔 `dirty`。Core 将这些字段规范化写入 ProvenanceRecord，未知诊断字段不会自动进入 provenance。Backend 在返回结果前失败时只能保留 Registry/ResolvedPlan 身份，真实源码、模型或容器身份必须由后续真实适配器在执行前固定并校验。

Geometry frontend 的相机集合按实际传给 reconstruction 的顺序持久化为 `CameraCollection` Artifact；它与 point cloud、每个 depth Artifact 都拥有独立 ProvenanceRecord。reconstruction mesh、材质纹理和 region map 的 `derived_from` 必须包含该 CameraCollection。集合输出使用稳定的端口索引身份（例如 `depths[0]`），避免同一节点的多个 Artifact 共享 ExecutionOutput identity。

## 5. Hybrid Provenance

`ComponentProvenance` 记录：

```text
component_id
artifact
source = observed | reconstructed | generated | mixed
provenance_ids
region_map (optional ArtifactRef, kind = quality_evidence)
```

有可靠区域映射时，`region_map` 可以指向 kind 为 `quality_evidence` 的独立证据 Artifact。没有可靠映射时只能将相应组件标为 `mixed`，不得伪造 face 或 vertex 级来源。`AssetDefinition.component_provenance` 是可选字段，旧资产保持可读。

存在 `region_map` 时，AssetRelease 必须将该 opaque 证据 Artifact 一并物化；Core 不解释其编码，但不能让发布后的 `asset.json` 留下只能依赖内部 Store 解析的证据引用。

`reconstruction@1` 输出的 component 必须引用其 native mesh。Canonicalization 只做刚体方向变换和统一尺度，不改变拓扑，因此 Core 将 component 的 artifact 引用显式重映射到 canonical mesh，并保留 source、provenance IDs 和已有 region map。若 component 引用其他 mesh，Core 拒绝输出；未来若引入改变拓扑的处理，必须定义新的 region map 转换契约。

当前执行基线不接受 Backend 自行填写无法在本 Run 中验证的 `provenance_ids`。Core 在 reconstruction 成功后为 native mesh 创建 reconstruction ProvenanceRecord，并将其 ID 注入每个 component；存在 region map 时，还会为该证据创建独立 ProvenanceRecord，并把第二个 ID 注入对应 component。assemble provenance 的派生关系同时包含 mesh 和 region map Artifact。材质引用的纹理必须是可解码的图像 Artifact；有纹理时 mesh 必须提供逐顶点 UV。默认 `apply_material` 模式下，ExportOperator 将 PBR 参数与纹理嵌入 `geometry/visual.glb`，同时把原纹理 Artifact 随 AssetRelease 物化。输出顶点颜色的 Backend 使用显式 `export_appearance_mode="preserve_mesh"` 保留 mesh 外观。

## 6. 跳过 Phase 5 的边界

geometry frontend 输出的 camera 是重建输入或估计结果，不等同于通过 Phase 5 CameraRegistration gate。Phase 6 在 Phase 5 延期期间必须保持：

- render-back QA 为 `skipped`；
- 不生成像素级一致性通过结论；
- 不把估计 camera 标成已注册 camera；
- 不用缺失 registration 阻塞结构和几何 QA。

## 7. 当前验收与延期项

首批基础完成条件：

- Multi RGB/RGBD manifest 可校验、导入并持久化；
- ObservationBundle 校验覆盖 view 对齐、尺寸、camera、depth frame/unit；
- collection cardinality 在编译期和运行期严格检查；
- multi-view Pipeline 静态编译通过；
- `ResolvedPlan` 同时绑定 geometry frontend 和 reconstruction；
- Fake Backend workflow 可端到端产生可加载 canonical GLB、AssetDefinition、AssetRelease 和 BuildRun；
- component provenance 在 canonicalization 后引用最终 canonical mesh；
- 既有单图 Pipeline 和 Phase 4 Backend 行为不回归。

以下事项等待产品或模型选择，不阻塞基础契约：

- 代表性静态物体多视图质量 benchmark（已有 DA3 4/8/16 帧资源 smoke 不替代质量验收）；
- TSDF 参数 profile、网格完整性与颜色质量边界（已有真实顶点颜色网格发布 smoke）；
- completion 触发条件、模型和融合方式；
- region provenance 的 face/vertex/sidecar 具体编码；
- video 抽帧、镜头切分和时间戳策略；
- RGBD 传感器格式、无效值和 metric scale policy；
- Phase 6 benchmark 数据集与质量阈值。

Backend 可以输出 region map Artifact 和组件 source；当前禁止的是注入未经 Core 验证的 provenance ID。未来 Scene-to-Asset Backend 若输出自己的来源图，需先定义证据验证及 Core 签发契约。

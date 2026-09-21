# AssetsGenerator

通用 Real-to-Sim 资产生成管线的设计与后续实现仓库。

## 推荐入口：节点工作台

从 [中文节点编辑器指南](docs/guides/node-editor.md) 开始：配置模型服务、连接节点、
上传输入、执行管线并查看模型。当前画布使用 React Flow。
可用范围与缺口见 [简短状态表](docs/reports/node-editor-b2-status.md)。
下文保留的 CLI 用于自动化、批处理和诊断；初次使用不必逐个学习。


当前实现覆盖完整单图到结构化 3D 资产流程，以及 Phase 6 的模型中立多视图 Core vertical slice：

```text
Single RGB
    + Optional Provided Mask
    -> Provided Mask or BiRefNet_lite Segmentation
    -> RGBA Prepare
    -> One Real Shape Backend
    -> Canonical GLB
    -> AssetDefinition / AssetRelease
    -> Provenance and Geometry QA
```

```text
Multi RGB / RGBD manifest
    -> Persisted ObservationBundle
    -> Geometry Frontend Backend contract
    -> Reconstruction Backend contract
    -> Canonical GLB + component provenance
    -> AssetDefinition / AssetRelease
```

当前开发主线是可编排节点工作台：React Flow 画布编辑、不可变 DAG 执行计划、
逐节点本地/HTTP Backend 绑定、人工选择和结果预览。使用方法见
[节点编辑器指南](docs/guides/node-editor.md)，已验收范围与剩余项见
[B2 状态核对](docs/reports/node-editor-b2-status.md)。
[ComfyUI 图片复合节点](docs/guides/comfy-image-profile.md)目前为实验接入，
已通过 CPU 协议与浏览器链路验证，尚未完成真实 ComfyUI 模型验收。

设计入口：

- [文档分类索引](docs/README.md)
- [Architecture](docs/design/architecture.md)
- [Asset IR](docs/design/asset-ir.md)
- [Coordinate System](docs/design/coordinate-system.md)
- [Pipeline Contract](docs/design/pipeline-contract.md)
- [Multi-view and Hybrid Contract](docs/design/multi-view-hybrid.md)
- [TripoSR Backend Contract](docs/design/triposr-backend.md)
- [TripoSR Environment and Preflight](docs/guides/triposr-environment.md)
- [Artifact Store](docs/design/artifact-store.md)
- [Evaluation](docs/design/evaluation.md)
- [Roadmap](docs/planning/roadmap.md)
- [Phase 3 已筛选输入基线报告](docs/reports/phase3-approved-baseline-v1.md)

## 当前实现

仓库已包含 Phase 2 单图流程、已验证关闭的 Phase 4 Backend 可替换性，以及 Phase 6 的模型中立基础：

- Blob、Artifact、StructuredValue、AssetDefinition、AssetRelease、Provenance 和 QualityReport schema
- 本地内容寻址 Artifact Store，支持 staging、digest 校验和原子提交
- 固定 `image_asset_v2` Pipeline 与精确端口契约检查
- 构建时按打包的 PipelineDefinition 调度，并验证每个节点的真实输入输出
- 成功与失败 BuildRun、完整 NodeAttempt 和可查询的 Run 索引
- 可选 provided mask；缺失时使用固定 revision 的 BiRefNet_lite 生成 binary mask
- 本地 Worker 的 queued/running/succeeded/failed/cancelled 状态、幂等键、取消和结构化错误
- BiRefNet 分割结果的内容寻址缓存；缓存命中记录为 `execution_mode=cache_hit`
- 确定性 `backend_native -> asset_canonical -> gltf_export` 变换
- GLB 可加载性、非空有限 Mesh、空间契约和 digest Geometry QA
- 本地独立进程 TRELLIS.2 Backend adapter
- Backend registry、不可变 `ResolvedPlan`、Pipeline 默认绑定和 CLI Backend 覆盖
- BuildRun 记录解析契约摘要、实际节点 Backend 名称和 Registry 声明版本
- 原子 AssetRelease 目录发布和 manifest identity 防篡改校验
- 严格的 ObservationBundle / CameraRecord / ComponentProvenance IR
- 原子多图/RGBD manifest 导入与稳定 observation identity
- `geometry_frontend@1`、`reconstruction@1` 和集合 cardinality 契约
- Fake Backend 驱动的多视图端到端 Core workflow，覆盖 canonicalization、QA、provenance 和 release

当前实现包含 `trellis2` 和 `triposr` 两个 Shape Backend。TripoSR 已完成独立环境预检、真实 GPU smoke、native frame 验证及飞机/自行车串行回归，Phase 4 已关闭；自动 QA 与运行证据见 [Phase 4 TripoSR 验证报告](docs/reports/phase4-triposr-validation-v1.md)。Phase 5 已显式延期。

Phase 6 已接入 DA3-Base geometry frontend 和 Open3D TSDF reconstruction Backend，主交付为带顶点颜色的三角网格；已完成 DA3 的 4/8/16 帧资源验证及真实 DA3 → Open3D → GLB 发布 smoke，见 [Open3D 验证报告](docs/reports/open3d-tsdf-smoke-v1.md)。Fake Backend 仅用于测试；代表性物体质量 benchmark、TSDF 参数 profile、completion 和正式质量阈值尚未完成。Phase 6 流程首版已经闭环，统一 CLI 提供多视图构建、独立生成候选、人工检查和运行查询，见 [Phase 6 CLI 指南](docs/guides/phase6-cli.md)。

Phase 7 首版已提供 SAM 未知类别 mask 候选、loopback 可视选择、逐对象 Shape Backend 生成、显式人工位姿和 SceneDefinition/场景 GLB 发布。真实双对象链路已使用 TripoSR 完成验收；该流程不宣称语义检测、自动位姿、尺度恢复或物理可用，见 [Scene to Assets 指南](docs/guides/scene-to-assets.md) 和 [Phase 7 报告](docs/reports/phase7-scene-v1.md)。

Phase 8 首个切片提供显式的 CPU convex-hull collision generation。它从 canonical visual
mesh 派生同 frame/unit 的 `collision_mesh`，发布新的不可变 AssetDefinition/AssetRelease，
并记录 collision QA 和 provenance；不执行 metric calibration 或刚体参数估计。使用见
[Collision Generation 指南](docs/guides/collision-generation.md)。

Phase 8 还提供显式两点距离的 metric scale calibration。调用方提供 canonical 空间中的两点
及真实米制距离，Core 对 visual/collision geometry 统一缩放并发布新的 meter-unit 资产；不从
图像或类别先验猜测尺度。使用见 [Metric Scale Calibration 指南](docs/guides/metric-scale-calibration.md)。

Phase 8 现已提供用户显式 dynamic rigid-body properties。输入必须是 meter-unit、带单一 collision
mesh 且尚无 physics 的资产；Core 验证质量、质心、惯性张量和接触参数的绑定与数学自洽，但不
估计或声称这些值物理准确。使用见 [Explicit Rigid Body Properties 指南](docs/guides/rigid-body-properties.md)。

标准 USD 导出接受上述 metric rigid-body release，保留 AssetDefinition 与来源证据，
新增 USD、包内纹理和 SDK 回读 QA。见 [USD 使用指南](docs/guides/usd-export.md)。
当前仅验证标准 OpenUSD，不宣称 Isaac Sim 导入或仿真通过。

安装开发环境并检查固定 Pipeline：

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/assets-generator compile-pipeline
.venv/bin/assets-generator compile-pipeline \
  --pipeline pipelines/multi_view_asset_v1.yaml \
  --operators pipelines/operators-v1.yaml
```

导入已经抽取并在 manifest 中显式关联的多图或 RGBD 观测：

```bash
.venv/bin/assets-generator import-observations \
  --manifest /path/to/observations.json \
  --store artifact-store
```

manifest 的每个 view 必须提供稳定 `view_id` 和 RGB image；mask、depth、camera 为可选字段，具体 schema 与校验规则见 [Multi-view and Hybrid Contract](docs/design/multi-view-hybrid.md)。Video 当前只接受调用方预先抽取的帧。

使用本机 TRELLIS.2 环境执行 Phase 2：

```bash
.venv/bin/assets-generator build \
  --image /path/to/input.png \
  --output outputs/example \
  --name example
```

默认适配器读取 `/home/ypkwsl/Workspace/TRELLIS.2`，并通过
`/home/ypkwsl/DevTools/miniconda3/envs/TRELLTS/bin/python` 启动独立进程。可使用
`--trellis-repo`、`--trellis-python` 和 `--trellis-model` 覆盖这些位置。Pipeline 默认将
`generate_shape` 绑定到 `trellis2`。配置 TripoSR 后，可显式传入
`--shape-backend triposr --triposr-python /path/to/python --triposr-repo /path/to/TripoSR --triposr-frame-validation /path/to/triposr-frame-validation.json`；
模型、超时、chunk size、marching-cubes resolution 和 foreground ratio 有独立参数。传入
`--mask /path/to/mask.png` 时直接复用该 mask；省略时，通过 `--segmentation-python`
指定的隔离环境运行 BiRefNet_lite。分割阈值和超时分别由
`--segmentation-threshold`、`--segmentation-timeout` 控制。
Core 与 Backend 目前通过临时 JSON 请求和本地 Artifact 路径通信，这是 Phase 1 的本地进程
过渡协议，不是可远程部署的 Worker 服务。TRELLIS Backend 默认超时为 1800 秒，可通过
`--backend-timeout` 调整。

发布目录包含：

```text
asset.json
release.json
geometry/visual.glb
qa/quality-report.json
provenance/*.json
run.json
```

模型权重、输入、生成资产、Artifact Store 和运行目录均不进入 Git。

### Pipeline versions

`image_asset_v2` is the supported default Phase 2 pipeline. `image_asset_v1.yaml` is retained as a historical Phase 1 baseline and is not selected by the default build command.

统一多视图重建、生成候选、人工检查及运行查询命令见 [Phase 6 CLI 指南](docs/guides/phase6-cli.md)。

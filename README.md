# AssetsGenerator

通用 Real-to-Sim 资产生成管线的设计与后续实现仓库。

当前实现覆盖完整单图到结构化 3D 资产流程：

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

设计入口：

- [文档分类索引](docs/README.md)
- [Architecture](docs/design/architecture.md)
- [Asset IR](docs/design/asset-ir.md)
- [Coordinate System](docs/design/coordinate-system.md)
- [Pipeline Contract](docs/design/pipeline-contract.md)
- [TripoSR Backend Contract](docs/design/triposr-backend.md)
- [Artifact Store](docs/design/artifact-store.md)
- [Evaluation](docs/design/evaluation.md)
- [Roadmap](docs/planning/roadmap.md)
- [Phase 3 已筛选输入基线报告](docs/reports/phase3-approved-baseline-v1.md)

## 当前实现

仓库已包含 Phase 2 单图流程和 Phase 4 的 Backend 可替换骨架：

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
- BuildRun 记录实际使用的节点 Backend 绑定
- 原子 AssetRelease 目录发布和 manifest identity 防篡改校验

当前实现包含 `trellis2` 和 `triposr` 两个 Shape Backend adapter。TripoSR 已通过隔离进程与 fixture 契约测试，但尚未完成独立环境和真实 GPU 验证；Router 资源策略和跨 Backend 比较报告尚未实现。

安装开发环境并检查固定 Pipeline：

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/assets-generator compile-pipeline
```

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
`--shape-backend triposr --triposr-python /path/to/python --triposr-repo /path/to/TripoSR`；
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

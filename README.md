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

- [Architecture](docs/architecture.md)
- [Asset IR](docs/asset-ir.md)
- [Coordinate System](docs/coordinate-system.md)
- [Pipeline Contract](docs/pipeline-contract.md)
- [Artifact Store](docs/artifact-store.md)
- [Evaluation](docs/evaluation.md)
- [Roadmap](docs/roadmap.md)

## 当前实现

仓库已包含 Phase 2 单图流程：

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
- 原子 AssetRelease 目录发布和 manifest identity 防篡改校验

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
`--trellis-repo`、`--trellis-python` 和 `--trellis-model` 覆盖这些位置。传入
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

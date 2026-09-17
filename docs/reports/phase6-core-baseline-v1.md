# Phase 6 多视图 Core 基线验证

报告日期：2026-09-16。范围：模型无关 Observation IR、多视图 Pipeline 契约、Backend 绑定、Fake Backend 端到端执行、provenance、BuildRun、QA 和 release。

## 结论

Phase 6 的模型无关 Core 基线已经闭环。仓库可以导入显式关联的 Multi RGB/RGBD manifest，持久化内容派生的 `ObservationBundle`，通过不可变 `ResolvedPlan` 绑定 geometry frontend 与 reconstruction Backend，并在 Fake Backend 测试中生成可加载的 canonical GLB、`AssetDefinition`、`AssetRelease` 和 `BuildRun`。

该结论只证明 Pipeline Core 契约和执行链可用。Fake Backend 不证明任何真实多视图模型可用；首个真实 geometry frontend、reconstruction 表示与 Backend、completion 策略、数据集和阈值尚未选定，因此本报告不关闭完整 Phase 6。

## 已验证范围

- `CameraRecord`、`ObservationView`、`ObservationBundle` 和 `ComponentProvenance` 使用强类型 IR；Observation identity 由规范化后的有序 view 内容派生。
- `import-observations` 原子导入 RGB、mask、depth 和 camera；验证 raster 语义、digest、尺寸、frame、unit、view 关联和刚体外参。
- collection cardinality 在 Pipeline 编译和 Runtime 中精确检查，不执行标量与集合的隐式包装或展开。
- Registry 和 `ResolvedPlan` 同时绑定 `geometry_frontend@1` 与 `reconstruction@1`，并把 Pipeline/OperatorSpec 契约摘要及 Backend 声明版本写入 BuildRun。
- 所有 Backend `ArtifactRef` 输出先验证 digest、carrier、kind、schema、frame 和 unit，再执行 camera、depth、material、component 和 native-frame 语义检查；契约失败记录为对应节点的 `contract_error`。
- reconstruction 实际使用的有序 CameraCollection、mesh、depth、point cloud、region map 和材质纹理具有独立 provenance；component 在 canonicalization 后引用最终 canonical mesh。
- region map 限定为 `quality_evidence`，随 Release 物化，并进入 component provenance IDs 与 assemble 派生关系。
- PBR texture 引用验证 digest、kind 和图像编码；ExportOperator 将材质因子与纹理写入 GLB，带纹理 mesh 必须提供 UV；纹理同时随 Release 物化，并进入 reconstruction、assemble 与 export provenance。
- Phase 5 延期边界保持：估计 camera 不视为通过 CameraRegistration，render-back 为 `applicable=false, status=skipped`。
- 单图 Shape Backend 路径使用相同的 Backend 输出校验顺序，Phase 4 行为没有回归。

## 验证命令与结果

在项目 `.venv` 中执行：

```text
.venv/bin/python -m pytest -q
206 passed

.venv/bin/ruff format --check .
56 files already formatted

.venv/bin/ruff check .
All checks passed!

.venv/bin/mypy src
Success: no issues found in 32 source files

.venv/bin/python -m build
Successfully built assets_generator-0.1.0.tar.gz and assets_generator-0.1.0-py3-none-any.whl

.venv/bin/python -m assets_generator.cli compile-pipeline \
  --pipeline pipelines/multi_view_asset_v1.yaml \
  --operators pipelines/operators-v1.yaml
multi_view_asset_v1@1: valid
```

仓库与包内的 `multi_view_asset_v1.yaml`、`operators-v1.yaml` 字节一致，`git diff --check` 通过。

## 明确延期项

以下事项需要模型、产品、传感器或数据集选择，不属于本次 Core 基线结论：

- VGGT、COLMAP 或其他首个真实 geometry frontend 及固定 revision；
- reconstruction 主交付表示和真实 Backend；
- completion 模型、触发条件和融合策略；
- region-map 的 face、vertex 或 sidecar 编码；
- video 抽帧、镜头切分和时间戳策略；
- RGBD 传感器格式、无效值与 metric scale policy；
- Phase 6 benchmark 数据集、指标和质量阈值；
- 真实 Backend 的独立环境、GPU 运行和同一 benchmark 验证。

这些事项确定前，不开放以 Fake Backend 为实现的多视图构建 CLI，也不声称 Phase 6 Backend 可生成可用资产。

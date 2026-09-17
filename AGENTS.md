# Agent Instructions

## 项目与当前阶段

- `AssetsGenerator / Real-to-Sim Asset Compiler` 将现实观测编译为可追溯、可校验的结构化 3D 资产。
- 当前进入 Phase 7 首版：提供 mask 的对象生成与显式场景装配；Phase 6 流程首版已闭环。Phase 4 已关闭，Phase 5 CameraRegistration/render-back QA 显式延期。
- DA3-Base 是首个真实 geometry frontend，Open3D TSDF 是首个 reconstruction Backend；已完成真实 DA3 → Open3D → GLB 发布 smoke，尚未完成代表性物体质量验收，也未关闭完整 Phase 6。
- 当前以流程闭环优先：Phase 7 首版提供 SAM 未知类别 mask 候选，但不宣称语义检测、位姿估计或物理可用；遵守 docs/design/scene-to-assets.md。Phase 6 首版已包含独立生成候选、人工对齐/检查、区域组合和标准 AssetRelease 基础 QA。完整 Phase 6 未关闭；质量调优、几何约束 completion、自动配准和拓扑融合显式延期。遵守 `docs/design/da3-backend.md`、`docs/design/open3d-backend.md` 和 `docs/design/multi-view-hybrid.md`。
- 单图 TRELLIS.2 / TripoSR 基线须保持；TripoSR 遵守 `docs/design/triposr-backend.md`。真实 Backend 使用独立环境，Fake Backend 或 smoke 不替代质量 benchmark。
- 当前不要扩展分布式调度、完整 FrameGraph、通用 schema migration 或与本阶段无关的平台能力。
- 主要目录：源码 `src/assets_generator/`，测试 `tests/`，Pipeline 配置 `pipelines/`，文档 `docs/`。

## 权威文档

按任务范围阅读：

- `docs/design/architecture.md`：系统边界和总体架构。
- `docs/design/asset-ir.md`：数据模型和身份语义。
- `docs/design/coordinate-system.md`：坐标系与导出转换。
- `docs/design/pipeline-contract.md`：Operator、Backend、DAG、BuildRun 和 Worker 契约。
- `docs/design/artifact-store.md`：持久化、寻址、原子提交和缓存。
- `docs/design/evaluation.md`：QA 和 benchmark。
- `docs/planning/roadmap.md`：阶段范围和实现顺序。

专项文档优先于架构总览；修改契约时同步修正相关高层表述。文档分类和导航规则见 `docs/README.md`。

## 必须保持的约束

- Pipeline Core 不包含模型代码、CUDA 依赖或冲突的 Python 环境；Backend 通过独立进程或容器隔离。
- `OperatorSpec` 是端口、kind、cardinality、schema、frame 和 unit 的唯一契约来源；Registry 只描述实现能力和运行要求，不复制端口定义。
- V1 `PortValue` kind 精确匹配；类型转换必须由显式 Operator 完成。
- 空间 Artifact 必须声明 `frame_id` 和 `unit`；Backend 必须输出 `BackendNativeFrame`。Canonicalization 的规则、版本、阈值和 tie-break 必须进入 provenance。
- Blob、Artifact 和 ExecutionOutput 的身份不得混用。URI 不参与身份；Artifact 不可变，任何转换都产生新 Artifact。
- `StructuredValue` 仅在跨 Run 引用、进入 Release、作为证据或需要独立 digest 时持久化。
- `AssembleAssetOperator` 只构造 `AssetDefinition`；`ExportOperator` 只负责格式转换和 `AssetRelease`。
- FakeBackend 只用于测试；Backend 可替换性最终必须用真实模型和同一 benchmark 验证。GPU benchmark 串行执行，避免资源竞争污染结果。
- 不提交模型权重、数据集、输入或生成资产、Artifact Store、运行目录、缓存和大体积二进制文件。测试 fixture 必须小型且可合法提交。

## 实现与验证

- 修改前阅读相关代码、测试和文档；保持改动聚焦，不覆盖用户未提交的修改。
- 行为变化必须有相应测试。新增持久化 schema 要有往返测试；空间转换要覆盖 frame、unit 和方向；Operator 要有端口契约测试。
- 常用检查：

```bash
python3 -m pytest
python3 -m ruff format --check .
python3 -m ruff check .
python3 -m mypy src
python3 -m build
```

- 未实际执行的检查不得声称通过；真实模型或 GPU 验证未运行时应明确说明。
- 文档放入 `docs/design/`、`docs/planning/`、`docs/guides/` 或 `docs/reports/`；移动文档时更新索引和引用。报告必须区分事实、观察和计划，并以 `<DATASET_ROOT>` 等占位符引用仓库外证据。

## Git

- 未经明确要求，不提交、改写历史、切换分支或推送。
- 提交信息使用 `<type>: <summary>`，其中 type 为 `feat`、`fix`、`docs`、`refactor`、`test` 或 `chore`。
- 每个提交只包含一个完整改动；禁止使用可能丢失工作内容的命令。

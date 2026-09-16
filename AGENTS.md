# Agent Instructions

## 项目配置

- 项目名称：`AssetsGenerator / Real-to-Sim Asset Compiler`
- 项目目标：将单图、多图、视频、RGBD 和 3DGS Scan 等现实观测编译为可复用、可追溯、可校验，并可逐步进入仿真系统的结构化 3D 资产。
- 当前阶段：设计规范 `v0.4.1` 已收口，下一步实现 Phase 1 单图资产 vertical slice。
- Phase 1 输入：单个主体对象的 `RGB + provided mask`。
- Phase 1 输出：包含 canonical GLB、`AssetDefinition`、`AssetRelease`、provenance 和 geometry QA 的静态可渲染资产。
- 核心技术栈：Python 3.10+、dataclass 与类型标注、YAML Pipeline 配置、glTF 2.0 / GLB、本地文件系统 Artifact Store；模型 Backend 使用独立进程或容器隔离。具体 schema 校验库在工程初始化时根据最小实现需要选择，不预设完整框架。
- 计划使用的开发与验证工具：pytest、Ruff、mypy、build。
- 源码目录：`src/assets_generator/`
- 测试目录：`tests/`
- Pipeline 配置目录：`pipelines/`
- 文档目录：`docs/`
- 构建命令：`python3 -m build`
- 测试命令：`python3 -m pytest`
- 格式化或检查命令：`python3 -m ruff format --check . && python3 -m ruff check . && python3 -m mypy src`

当前仓库尚未创建 `pyproject.toml` 和生产代码，当前环境也尚未安装上述开发工具。初始化工程时应按上述目录和命令建立配置；在初始化完成前，不能声称构建、测试或静态检查已执行。

## 项目权威文档

开始实现或修改契约前，按任务范围阅读以下文档：

- `docs/design/architecture.md`：系统目标、总体架构、V1 边界和验收标准。
- `docs/design/asset-ir.md`：Blob、Artifact、ExecutionOutput、AssetDefinition 和 AssetRelease。
- `docs/design/coordinate-system.md`：坐标系、Frame、canonicalization 和 Export Profile。
- `docs/design/pipeline-contract.md`：PortValue、OperatorSpec、Backend、DAG、BuildRun 和 Worker 契约。
- `docs/design/artifact-store.md`：内容寻址、位置解析、原子提交和缓存语义。
- `docs/design/evaluation.md`：Geometry QA、QualityReport、render-back 和 benchmark。
- `docs/planning/roadmap.md`：分阶段范围和模型接入顺序。

契约存在冲突时，以更具体的专项文档为准，并同步修正 `docs/design/architecture.md` 中的高层表述。

## 项目特定约束

- 当前工作重点是完成真实 Phase 1 vertical slice，不继续增加与 Phase 1 无关的通用基础设施。
- Phase 1 固定流程为：`RGB + Mask -> RGBA Prepare -> Shape Backend -> Canonicalization -> AssembleAsset -> GLTF2 Export -> Geometry QA`。
- Phase 1 只接入一个真实 Shape Backend。第二个 Backend、Model Registry、Router 和 ResolvedPlan 必须在 benchmark 基线建立后再加入。
- FakeBackend 只能用于契约测试，不能替代真实 Backend 的端到端验证。
- OperatorSpec 是输入输出端口、kind、cardinality、schema、frame 和 unit 要求的唯一契约来源；Registry 不得复制这些基础端口定义。
- V1 使用封闭的 PortValue kind 和精确匹配。类型转换必须由显式 Operator 完成，不建立通用继承体系。
- Backend 必须显式输出 `BackendNativeFrame`。空间 Artifact 必须声明 `frame_id` 和 `unit`。
- Phase 1 只实现 `backend_native -> asset_canonical -> gltf_export` 线性 frame 链，不实现完整 TF、复杂图搜索或闭环优化系统。
- Canonicalization 必须使用文档规定的确定性规则，并将规则版本、阈值和 tie-break 写入 provenance。
- Blob、Artifact 和 ExecutionOutput 的身份不得混用。URI 是可变访问位置，不参与内容或 Artifact identity。
- Artifact 不可变。任何 repair、canonicalize、export 或其他处理都必须创建新 Artifact，禁止覆盖输入 Artifact。
- `StructuredValue` 只有在跨 Run 引用、进入 AssetRelease、作为证据或需要独立 digest 时才持久化为 Artifact。
- `AssembleAssetOperator` 只组装 `AssetDefinition`；`ExportOperator` 只负责目标格式转换和 `AssetRelease`。
- V1 mandatory 是 Visual Mesh、Renderable Material、Deterministic Local Frame、Bounding Box、Provenance、Build Metadata、Geometry QA 和 GLTF2 Export。
- Semantic Label、完整 PBR、Gaussian、Collision、Metric Scale 和 Render-back QA 在 V1 中均为 optional。
- 未请求 Collision 或尚未实现 camera registration 时，对应 QualityCheck 必须为 `applicable=false`、`status=skipped`，不能导致整体验收失败。
- 未经校准的模型数值使用 `score`；只有具备 method、version、calibration domain 和 evidence 的值才能命名为 `confidence`。
- 模型依赖、CUDA 扩展和互相冲突的 Python 环境不得安装到 Pipeline Core 环境中，应由 Backend 进程或容器隔离。
- 不提交模型权重、输入资产、生成资产、Artifact Store 内容、运行目录、缓存或大体积二进制文件。测试 fixture 必须小型、明确授权并适合进入 Git。
- 新增持久化 schema 时必须有序列化往返测试；新增空间转换时必须有 frame、unit 和方向测试；新增 Operator 时必须有端口契约测试。

## 工作原则

- 修改前先阅读相关代码、测试和项目文档，遵循仓库已有设计与命名习惯。
- 保持改动聚焦于当前任务，不进行无关重构、格式化或依赖升级。
- 优先复用项目已有的框架、工具和辅助函数；仅在确实降低复杂度时新增抽象。
- 不猜测缺失的业务规则。能够从代码和文档确认时先自行调查，关键条件仍不明确时再询问。
- 不覆盖、撤销或删除用户已有的未提交改动。
- 如果因为权限或其他问题导致某一项工作未能执行或需要用户协助，要说明白产生问题的原因，以及希望用户怎么做，最好提供可直接运行的命令。

## 目录与文件

- 新文件放入职责匹配的现有目录，不随意增加新的顶层目录。
- 文档必须按职责归类：`docs/design/` 存放架构和契约，`docs/planning/` 存放路线图与实施计划，`docs/guides/` 存放使用和操作指南，`docs/reports/` 存放注明日期、数据范围及证据的评测和审查报告。
- `docs/` 根目录只保留文档索引 `README.md`；新增文档优先使用现有分类。确需新增分类时，按长期职责命名，并同步更新索引。
- 新增、移动或重命名文档时，必须同步更新 `docs/README.md`、项目 README、AGENTS.md 及其他引用位置，并检查相对链接有效性。
- 文档应独立于对话，描述可验证的事实、契约、方法、证据、限制和可复用的操作步骤；不得写入聊天过程、助手行动汇报或临时承诺（如“无新增 GPU 运行”“我接下来会……”）。计划应明确标为计划，实验观察与推测必须区分。
- 提交评测报告时，用 `<DATASET_ROOT>` 等明确的路径约定引用本地数据，记录关键证据摘要；数据和生成资产留在仓库外。报告通过 `docs/README.md` 索引，不作为新的契约来源。
- 生产代码与测试代码保持清晰对应。
- 临时文件、构建产物、缓存和本地配置不得提交到仓库。
- 不提交密钥、令牌、密码或包含敏感信息的日志。

## 实现与验证

- 行为变化应补充或更新测试；测试范围与改动风险相匹配。
- 只在逻辑不易理解时添加简短注释，避免解释代码表面行为。

## Git 约定

- 提交信息使用 `<type>: <summary>` 格式。
- `type` 使用 `feat`、`fix`、`docs`、`refactor`、`test`、`chore` 之一。
- 每个提交只包含一个完整、可说明的改动，不混入无关文件。
- 未经明确要求，不创建提交、修改历史、切换分支或推送远程。
- 禁止使用 `git reset --hard` 等可能丢失工作内容的命令，除非用户明确授权。

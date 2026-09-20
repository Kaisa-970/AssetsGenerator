# 模块化资产 Pipeline 开源框架评估

日期：2026-09-18。状态：源码、官方文档及仓库资料调研；未安装或运行候选框架，未做性能或真实模型兼容验收。结论为技术选型建议，不代表已批准迁移。

## 目标与结论

目标是通过拖入模块、配置模型和连接有类型的端口，组合图片、mask、深度、相机、点云、网格及资产发布流程；同时支持独立本地环境和远程模型服务，保留人工决定、空间语义、运行恢复及 Artifact provenance。

建议采用 **React Flow 画布 + AssetsGenerator Core + 本地/远程 Backend 适配器**。不自行实现画布交互，不直接替换资产执行与证据体系。ComfyUI 可作为独立实验工具及远程复合 Backend 的候选。完整执行平台暂不引入；未来有跨机器调度需求时再评估 Prefect 等。

这不是“接个画布就完成”：任意 DAG 执行、模块注册、服务任务恢复等仍需实现，必须先用小范围原型验证成本。

## 现有代码能复用什么

- `pipeline.py::compile_pipeline` 已校验 DAG 环路、绑定、kind、carrier、schema、cardinality，但返回 None，不执行任意图。
- `backend_registry.py::ResolvedPlan` 已绑定具体 Backend；它不是通用持久任务调度器。
- `runtime.py::Phase1Runtime.run_node` 可复用单节点校验及执行记录。
- `workflow.py` 仍显式顺序调用节点；`workbench_engine.py` 仍构造固定阶段并按 propose/select/generate 分派。
- 可复用 Artifact Store、空间转换、发布流程、人工决定证据、独立进程门控；不能据此声称任意图恢复已经存在。
- 当前页面为原生 HTML/JS。采用 React Flow 需要引入独立前端构建，产物再由 Python 服务托管；不把 npm/Node 变成运行模型的依赖。

## 候选比较

| 方案 | 对本项目的价值 | 必须自行补充的边界 | 建议 |
| --- | --- | --- | --- |
| React Flow / xyflow | MIT；节点、端口、连线、自定义面板、图保存恢复、交互校验 | Python 执行、领域契约、人工证据、恢复；布局需额外工具或逻辑 | 首选画布 |
| Rete.js 2 | MIT；模块化节点编辑，支持多种渲染栈，并有 dataflow/control-flow 引擎 | 官方说明导入导出不是开箱即用；JS 引擎不能直接替代 Python 资产执行 | 备选，尤其不采用 React 时 |
| LiteGraph.js | Canvas 节点图、序列化，接近传统蓝图体验 | 领域表单/可访问性等需自行验证；Comfy 分支已迁入其 frontend，不宜依赖旧独立分支 | 不优先 |
| ComfyUI | 成品视觉模型工作流、API、丰富节点生态，已有社区 3D 节点 | 环境隔离、空间与集合语义、持久人工决定及本项目 provenance 需适配 | 实验工具/独立服务 Backend |
| Langflow | MIT；可视编辑、API、自定义 Python 组件 | 主方向为 LLM/agent，资产类型、3D 预览和隔离执行仍要建设 | 相比画布库额外价值不足 |
| Prefect | Apache-2.0；Python 编排、人工暂停恢复、worker、部署与运行观测 | 无目标所需的资产端口编辑器；其 Artifact 概念不是本项目不可变资产身份 | 日后调度候选 |
| Dagster | Apache-2.0；数据资产依赖、分区、运行观测与 lineage | 不是模型蓝图画布；业务资产及人工操作仍需适配 | 当前不迁移 |

许可只记录所查项目本体，不涵盖模型权重、社区插件、商业扩展或具体组合分发条件。

## 为什么不直接采用 ComfyUI 作为全部内核

它最贴近用户“组合模型复现实验”的目标，值得保留为认真候选。但以下差异真实存在：

1. 自定义节点通常在服务 Python 进程内加载，模型环境冲突不会自动消失。可写 subprocess/HTTP 节点隔离，但仍需实现。
2. 自定义类型连接不等于 Artifact kind、schema、frame、unit 和相机对应关系校验。
3. 官方列表语义包含按元素执行及短列表末元素重复补齐；本项目要求显式 cardinality 和转换，不能原样沿用到深度/相机集合。
4. 队列和历史记录不能直接等价为本项目的耐久人工决定、子运行验收与孤儿进程恢复。
5. Core 与官方 frontend 使用 GPL-3.0。作为独立服务调用和直接复制/修改集成的分发边界不同，不能把两者混为一谈。

建议接入方式：将固定版本的 Comfy workflow 包装成复合 Backend。输入输出均经 Core 导入和验证，记录 workflow 摘要、节点/模型版本及实际可得的执行身份。只有外层执行证据时明确记录为复合 Backend，不伪造内部各节点的可验证 provenance。社区 TRELLIS2/3D-Pack 节点的存在不证明其能在现有环境稳定运行。

## 建议架构

```text
React Flow 编辑器
    ↓ 草稿图 + Backend 配置引用 + 参数
Core 编译/校验 → 不可变执行计划
    ↓
DAG 执行器 → 人工待办 / Core 操作 / Backend 适配器
                                      ├─ 本地独立进程
                                      └─ 远程服务（可包括 ComfyUI）
    ↓
Artifact Store + BuildRun + Provenance → 预览与标准资产发布
```

`OperatorSpec` 仍是端口唯一来源，由服务生成节点目录；Backend 注册表提供实现能力与参数描述，不复制端口契约。画布位置独立于执行身份。后端重新校验所有连线，前端校验只用于交互反馈。

远程适配器须定义上传/下载、job_id、超时、幂等提交和重连查询。远端是否能取消或恢复必须如实声明，不照搬本地 PID/进程组机制。服务无法提供模型 revision 时记录身份不可核实，不以 endpoint URL 冒充模型版本。密钥不写入可共享的管线文件。

支持新论文模型不应要求先修改执行器；通过显式注册 Operator/Backend 及必要转换扩展。类型集合扩展需要修改契约并验证，首版不以万能 Any 绕过类型系统。不运行浏览器上传的任意 Python 插件。

## 修订后的实施里程碑与退出条件

根据 review，将原来同时包含画布、DAG、HTTP、持久化和恢复的原型拆为两个里程碑。以下是拟实现与验收要求，不是当前能力声明。

### 里程碑 A：无画布的通用 DAG Core

- `compile_pipeline()` 返回不可变 `CompiledPlan`，包含节点实例、明确的输入绑定、依赖及解析后的执行配置；不只是校验。嵌套配置也必须不可变，不能仅冻结最外层 dataclass。具体序列化契约在实施设计中确定，与现有 `ResolvedPlan` 职责保持一致、不重复定义 Backend 身份。
- 引入 `AdapterRegistry`，根据明确的适配器身份调用实现，替代按 stage_id 名称分支执行。端口依旧只由 `OperatorSpec` 定义。
- 调度器按依赖就绪执行，支持扇出和汇合。逻辑分支不要求并行 GPU，首版可以串行调度。
- 已完成输出保存精确 `ArtifactRef`，汇合直接绑定对应输出，禁止按目录或“最新结果”猜测。人工节点复用现有等待、草稿、确认及证据机制。
- 用 YAML 定义并运行单图流程。初期单图资产内部步骤可保持显式复合节点，不宣称它们都已自由编排。
- 不接入 React Flow 或真实 HTTP/ComfyUI Backend；本地 Fake Backend 先验收，再用已有真实环境串行 smoke。旧工作台和历史记录兼容须验证。

专门的菱形图是必需验收，不以单链跑通代替：

```text
A → B → D
 \→ C →/
```

必须证明：

1. A 只执行一次；B、C 接收完全相同的 A 输出 ArtifactRef。
2. D 在 B、C 都成功且输入关系校验通过后才执行，输入不因完成顺序发生错配。
3. 任一分支失败时 D 不执行，失败和依赖阻塞可区分；成功分支结果保留。
4. 服务重启后验证已完成结果与依赖，显式重试失败分支时不重跑已验证完成的 A 和成功分支；损坏证据不恢复为成功。
5. 包含人工节点的图在等待时重启，恢复原待办；响应丢失不导致重复确认或重复派发。
6. 同一 Operator 可有两个不同节点实例，参数、Backend 绑定及 provenance 不串用。

### 节点实例与汇合语义边界

节点实例 ID 与 Operator 身份分离。Backend 绑定和参数属于节点实例；provenance 至少记录节点实例 ID、Operator 版本、Backend 身份，并保留 run/attempt 和多值输出元素身份。重命名或复制节点不得导致输出实例身份碰撞。

汇合不能仅检查端口类型。多输入可能要求共享 observation、兼容 frame/unit、相机与深度一一对应、相同数量或特定 lineage。关系要求由 Operator 契约声明，引用明确、版本化的 relation validator，执行器在执行前对实际输入校验。可静态确定的条件编译时拒绝，其余运行时检查；验证器缺失或校验失败均不派发 Backend。

首版只开放已定义关系规则的汇合；语义上独立的输入也必须明确声明独立，而不是缺省认为可任意组合。不得用端口类型相同或字段存在替代跨输入关系验证。验证器版本应进入计划契约身份，防止规则变化后误用恢复结果。

### 里程碑 B：React Flow 编辑器

A 验收通过后再接入真实执行状态：

- 从 `OperatorSpec`、`AdapterRegistry` 和 Backend 参数描述生成节点目录、端口及参数表单，不在前端复制一套端口契约。
- 支持拖放节点、多个同类实例、连线、参数配置和管线保存/加载。前端检查静态兼容性，后端重新编译才是权威。
- 渲染实例级运行状态，人工节点打开现有审查组件；画布位置与执行身份分开，执行后修改配置创建新计划/运行。
- Backend 选择扩展为本地独立环境、HTTP 服务和复合 ComfyUI workflow；只展示已配置、已实现适配器的选项。
- 分别验收编辑器和远程适配器。编辑器先接 A 的本地 Backend；HTTP 先用模拟服务验证幂等提交、job_id 重查及响应丢失，再做真实服务 smoke；ComfyUI 后续单独接入，不让模型服务安装成为画布验收的前置条件。

A 完成前允许纯前端、无执行的 React Flow spike，验证节点、端口和表单技术栈；不得写入生产 BuildRun、触发模型或维护第二套真实运行状态。

### ComfyUI 复合 Backend 的证据边界

视作不透明复合算子：输入输出在边界重新导入和校验，记录实际执行的 workflow JSON 摘要、ComfyUI 版本、自定义节点及模型身份、参数和远端 job_id。拿不到的身份明确标为未核实；内部 provenance 无法完整验证时，外层成功只证明边界产物与本次调用通过验收，不代表内部全过程可追溯。界面和报告均须保留这一限制。

论文复现需要对齐预处理、权重、参数与后处理，连线相同并不保证结果等价。跨运行缓存、分布式调度和任意插件上传不纳入上述里程碑。

## 来源

以下均为本次调研引用的官方文档/仓库；未锁定提交的在线页面后续可能变化。

- React Flow：[仓库及许可](https://github.com/xyflow/xyflow)、[保存/恢复](https://reactflow.dev/examples/interaction/save-and-restore)、[连线校验](https://reactflow.dev/examples/interaction/validation)、[布局](https://reactflow.dev/learn/layouting/layouting)。基础画布不要求购买 Pro。
- Rete：[仓库](https://github.com/retejs/rete)、[导入导出](https://retejs.org/docs/guides/import-export/)。
- LiteGraph：[上游](https://github.com/jagenjo/litegraph.js)、[Comfy 分支迁移说明](https://github.com/Comfy-Org/litegraph.js)。
- ComfyUI：[Core](https://github.com/Comfy-Org/ComfyUI)、[frontend](https://github.com/Comfy-Org/ComfyUI_frontend)、[列表语义](https://docs.comfy.org/custom-nodes/backend/lists)、[服务路由](https://docs.comfy.org/development/comfyui-server/comms_routes)。
- 社区节点，仅核对项目存在和说明：[ComfyUI-Trellis2](https://github.com/visualbruno/ComfyUI-Trellis2)、[ComfyUI-3D-Pack](https://github.com/MrForExample/ComfyUI-3D-Pack)。
- Langflow：[仓库](https://github.com/langflow-ai/langflow)。
- Prefect：[仓库](https://github.com/PrefectHQ/prefect)、[交互式工作流](https://docs.prefect.io/v3/advanced/interactive)、[workers](https://docs.prefect.io/v3/concepts/workers)、[artifacts](https://docs.prefect.io/v3/concepts/artifacts)。
- Dagster：[仓库](https://github.com/dagster-io/dagster)、[软件定义资产](https://docs.dagster.io/guides/build/assets)。

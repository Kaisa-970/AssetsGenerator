# 通用 DAG 编排与节点编辑器实施设计

**状态**：A1 静态编译、CPU／人工执行、受控进程及单图 YAML/审查切片已实现；真实 GPU 验收、完整里程碑 A 与节点编辑器尚未完成\
**目标版本**：Generic DAG Core v1 / Node Editor v1\
**适用范围**：本地单机执行、人工等待与恢复、独立 Backend 进程或 HTTP 服务\
**不构成证据**：本文不表示现有 Pipeline 已由通用 DAG 执行器驱动，也不表示 React Flow、HTTP Backend 或 ComfyUI 已接入。

## 1. 目标

本设计把现有固定工作台推进为两层能力：

1. 由后端权威编译和执行任意无环 Pipeline 图；
2. 在该执行能力验收后，用 React Flow 提供图的编辑与观察界面。

目标架构为：

```text
React Flow 编辑器
        ↓ DraftGraph
Core 编译、静态校验和 Backend 解析
        ↓ immutable CompiledPlan
通用 DAG Scheduler
        ├── Core Adapter
        ├── Human Adapter
        ├── 本地隔离 Backend
        └── HTTP / ComfyUI Composite Backend
        ↓
Artifact Store + BuildRun + Provenance + QualityReport
```

图形界面不是执行权威。所有保存的图在运行前必须由 Core 重新编译；运行依据是不可变 `CompiledPlan`，而不是浏览器中的节点或连线状态。

## 2. 当前代码事实

以下能力已经存在，可以复用：

- `compile_pipeline()` 能从端口引用推导依赖，检测环，并校验 kind、carrier、schema 和 cardinality。
- YAML Pipeline 已能表达扇出和汇合，例如同一输出供多个下游使用。
- `OperatorSpec` 是端口契约的唯一来源。
- `ResolvedPlan` 能绑定 Pipeline/OperatorSpec 摘要和 Shape Backend 实现。
- Artifact Store、`BuildRun`、provenance、人工决定、耐久状态写入及独立进程门控已有实现基础。
- 固定节点工作台能持久化 stage 状态，并在人工等待期间恢复。

以下能力尚不存在：

- `compile_pipeline()` 只校验，不返回可执行计划。
- 主要 Workflow 仍在 Python 中手写调用顺序。
- 固定工作台按 `propose`、`select`、`generate` 等 stage ID 分派实现。
- 跨端口语义约束主要散落在 Workflow 实现中，未成为可版本化的编译契约。
- 尚无生产 React Flow 前端、通用 HTTP Backend 适配器或 ComfyUI 集成。

因此，第一阶段的主体是通用 DAG Core，不是画布接入。

## 3. 范围与非目标

### 3.1 v1 范围

- 编译确定的有向无环图。
- 多个相同 Operator 的独立节点实例。
- 串行 ready-node 调度；数据依赖允许扇出和汇合。
- Core、人工、本地隔离进程和 HTTP Backend 的统一适配器边界。
- 节点级耐久状态、精确 ArtifactRef 输出、失败恢复和显式重试。
- 版本化的跨输入关系校验。
- 首先迁移 `image_asset_v2`，随后以不修改 Scheduler 为条件迁移 `multi_view_asset_v1`。
- React Flow 编辑、保存、加载和状态展示。

### 3.2 非目标

- 循环、动态展开、条件分支或通用 Workflow 语言。
- 分布式 Scheduler、跨机器锁、抢占式 GPU 调度。
- 浏览器上传并运行任意 Python 插件。
- 通用 `Any` 端口或隐式类型转换。
- 让 React Flow 或 ComfyUI 接管 Artifact、BuildRun、provenance 和恢复语义。
- 首版并行执行多个 GPU 节点。
- 因接入画布而重写现有模型算法或 QA 规则。

## 4. 图与计划模型

### 4.1 DraftGraph

`DraftGraph` 是可编辑输入，可以包含 UI 布局和未完成配置。它不是运行证据。

```python
@dataclass
class DraftGraph:
    graph_id: str
    name: str
    inputs: dict[str, DraftInput]
    nodes: dict[str, DraftNode]
    edges: list[DraftEdge]
    ui_layout: dict[str, object]
```

节点实例至少包含：

```python
@dataclass
class DraftNode:
    node_id: str
    operator: str              # e.g. shape_generation@1
    adapter: str               # e.g. shape_backend@1
    backend: str | None        # e.g. trellis2
    parameters: dict[str, object]
```

`node_id` 只标识本图中的实例，与 Operator 名称、Backend 名称和显示标题分离。同一 Operator 可以出现多次，并拥有不同参数和 Backend。

### 4.2 CompiledPlan

编译成功后产生不可变、可序列化的 `CompiledPlan`：

```python
@dataclass(frozen=True)
class CompiledPlan:
    plan_id: str
    pipeline_name: str
    pipeline_version: str
    inputs: FrozenMap[str, CompiledInputSpec]
    nodes: tuple[CompiledNode, ...]
    topological_order: tuple[str, ...]
    dependencies: FrozenMap[str, tuple[str, ...]]
    dependents: FrozenMap[str, tuple[str, ...]]
    contract_digests: FrozenMap[str, str]
    schema_version: str
```

`CompiledNode` 保存：

- 节点实例 ID。
- Operator 名称、版本和契约摘要。
- Adapter 名称、版本和实现契约摘要。
- 已解析 Backend 身份、能力声明和模型身份要求。
- 规范化输入绑定。
- 规范化参数。
- relation validator 名称、版本和摘要。
- 人工节点或计算节点分类。

`CompiledInputSpec` 保存每个 `pipeline.inputs` 的不可变 `PortSpec` 投影，包括 kind、carrier、
cardinality、schema、frame/unit 要求和 persist 语义。计划保存输入契约，不保存某次运行的实际
输入值；实际 `ArtifactRef` 或 `StructuredValue` 由 BuildRun 保存。

不可变性必须覆盖嵌套字段。不能只冻结最外层 dataclass，同时保留可变 `dict` 或 `list`。序列化时使用确定性顺序和现有 canonical digest 规则。

### 4.3 计划身份

`plan_id` 至少由以下稳定内容计算：

- Pipeline 名称和版本。
- 规范化的 Pipeline 输入规格及其契约摘要。
- 节点实例 ID、Operator、Adapter、Backend 绑定和参数。
- 规范化输入绑定与边。
- Operator、Adapter、Backend capability 和 relation validator 的契约摘要。
- 编译器 schema/version。

以下内容不参与执行身份：

- 画布坐标、缩放、颜色和折叠状态。
- UI label。
- 文件路径、URI、时间戳、run ID。
- 日志展示设置。

修改节点参数、Backend 或边会创建新的 `CompiledPlan` 和新运行。已完成历史运行不原地改写。

## 5. 输入绑定与数据身份

编译后的每个输入绑定只能引用：

```text
pipeline.inputs.<name>
<node_id>.outputs.<port_name>
```

可选输入必须在绑定中显式标记，不通过不存在的路径或空字符串表达。运行时解析后保存实际 `ArtifactRef` 或受契约允许的 `StructuredValue`，不得通过输出目录、文件时间或“最新结果”推测。

扇出时，一个上游输出只持久化一次。所有下游保存并接收完全相同的 `ArtifactRef`，不能复制字节后生成新的 Artifact identity。

集合输出必须保留元素顺序和每个元素身份。Scheduler 不使用短列表补齐、广播或隐式 zip；需要配对、广播或重排时使用显式 Operator。

## 6. AdapterRegistry 与 Backend 解析

### 6.1 职责划分

```text
OperatorSpec     定义 WHAT：端口、kind、cardinality、schema、frame、unit
AdapterSpec      定义 HOW：调用哪个 Core 实现或 Backend 协议、参数 schema、恢复能力
BackendRegistry  定义 WHERE/WHICH：具体实现、环境、模型与资源能力
```

Registry 不复制 Operator 的输入输出定义。

### 6.2 AdapterRegistry

每个 Adapter 以明确的名称和版本注册：

```python
class NodeAdapter(Protocol):
    spec: AdapterSpec

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult: ...
    def recover(self, context: NodeRecoveryContext) -> NodeRecoveryResult: ...
```

`AdapterSpec` 至少声明：

- adapter name/version。
- 支持的 OperatorSpec。
- 参数 schema 和默认值。
- 支持的 Backend 类别或 capability。
- 是否可能进入人工等待。
- 是否支持幂等查询、恢复、主动取消。
- 实现契约摘要。

Scheduler 只通过 AdapterRegistry 分派，禁止根据 `node_id`、显示名称或固定 stage 顺序选择实现。

### 6.3 Backend 解析

编译阶段把节点请求解析为具体 Backend 绑定。无法满足 Operator、profile、资源或平台要求时编译失败。Backend 的模型身份、运行环境和确定性声明进入执行证据；endpoint URL 不是模型身份。

密钥、令牌和本机凭据只保存引用，不进入可共享图定义和计划 identity。

## 7. 编译流程与三层验证

编译按以下顺序执行：

1. 解析 DraftGraph、Pipeline input 和节点实例。
2. 验证节点 ID、端口引用、必填参数和未知字段。
3. 推导依赖与反向依赖，检测自环和环。
4. 确定稳定拓扑顺序；同层节点使用 node ID 作为确定性 tie-break。
5. 按 `OperatorSpec` 校验静态端口兼容性。
6. 解析 Adapter 和 Backend，并固定版本与摘要。
7. 解析 relation validators，执行可静态判断的关系校验。
8. 规范化计划并计算 `plan_id`。

验证分三层，不能只靠端口 kind：

### 7.1 静态端口兼容

由 `OperatorSpec` 定义并在编译时检查：

- kind、carrier、schema 和 cardinality。
- `requires_frame`、`requires_unit`。
- 显式 optional 绑定。

### 7.2 跨端口关系约束

Operator 可声明版本化 relation validator：

```yaml
relations:
  - validator: matched_camera_depth_observation@1
    inputs: [images, cameras, depths]
```

典型约束包括：

- 多个输入来自同一 ObservationBundle 或同一前端运行。
- camera、depth 和 image 数量及顺序一致。
- frame、unit 和 transform chain 兼容。
- join 的两条分支针对相同源资产或明确允许独立来源。

验证器名称、版本和摘要进入 `CompiledPlan`。复杂 join 未声明关系语义时不开放自由连接；语义上相互独立也应由显式 validator 声明，而不是缺省放行。

### 7.3 运行时数据验证

真实 Artifact 解析后、Adapter 派发前再次验证：

- digest 和 schema 可读取。
- identity metadata 满足 frame/unit 要求。
- relation validator 对实际 lineage 和集合元素成立。
- optional 缺失符合 Operator 语义。

失败时节点标为 validation failure，不启动 Backend。

## 8. Scheduler 与状态机

### 8.1 Ready-node 调度

首版采用单写者、串行调度：

1. 加载 `CompiledPlan` 和最新耐久运行状态。
2. 按计划重新解析所有已成功节点的实际输入，核对已记录的 `resolved_inputs` 和
   `input_digest`，并验证其依赖证据。
3. 验证所有已成功节点的输出证据。
4. 找出依赖全部成功且自身为 `pending` 的节点。
5. 按拓扑顺序选择一个节点，解析实际输入并计算 `input_digest`。
6. 对计算节点执行跨运行 compute admission 检查。
7. 原子持久化 attempt 和运行状态后派发 Adapter。
8. 保存精确输出，再推进下一个 ready node。

串行执行不限制图的拓扑能力，也避免首版产生 GPU 资源竞争。以后增加并行时不得改变 Artifact 和恢复语义。

这里的串行不仅指单个 BuildRun 内一次只派发一个计算节点。必须保留现有 `_admit_compute()`
的跨运行准入语义：在同一服务管理范围内，只要任一旧运行存在已经授权且仍活动、状态未知或
无法可靠证明退出的 Backend 进程，新的 GPU 计算节点就不能获得执行放行。单个运行的文件锁
不能替代这项全局准入检查。

### 8.2 节点状态

节点实例状态至少包括：

```text
pending
running
waiting_for_input
succeeded
failed
interrupted
blocked
recovery_blocked
```

`blocked` 表示依赖失败或证据无效，区别于本节点执行失败。依赖修复并显式重试后可重新计算 blocked 状态。

`recovery_blocked` 表示 Scheduler 在恢复或派发前无法证明继续执行安全。它是当前 stage/run 的
耐久状态，不改写原 attempt 的历史结果。例如原 attempt 仍保存 `status=succeeded`，但恢复时发现
其输入绑定、输出 Artifact 或依赖证据失效，当前 StageState 进入 `recovery_blocked`。阻塞记录至少
包含 node ID、reason code、证据说明、发现时间和解除动作；不得只存在于服务内存或 UI 日志。

每个 attempt 保存：

- node instance ID、attempt number。
- Operator、Adapter、Backend 实际身份。
- resolved inputs 和 `input_digest`。
- 参数和关系验证器摘要。
- 开始/结束时间、execution mode、error code。
- 精确 outputs。
- child run、command receipt、worker execution 等适用证据。

恢复定位实现保存 Artifact → node/attempt/port/role 反向索引；无可定位消费者的损坏证据记录为运行级阻塞。索引不替代引用闭包校验，历史记录不能通过索引被豁免为有效输入。进程适配器保存每条命令的 worker 证据；工作台和 DAG 共用准入检查，已确认退出的进程另存按完整身份寻址的终态证据，避免重用 PID 被再次视作旧进程。

### 8.3 BuildRun 与计划

`CompiledPlan` 记录“将执行什么”；`BuildRun` 记录“实际发生什么”。BuildRun 引用 `plan_id`，但不得用计划内容替代实际输入、输出和 Backend 证据。

现有工作台的耐久状态迁移函数、OS 文件锁、fsync 写入、子运行登记和进程身份机制应被复用或抽取，不能在通用 Scheduler 中另建一套较弱状态。

### 8.4 独立分支与整体状态

一个分支进入 `waiting_for_input`、`failed` 或 `interrupted` 后，Scheduler 仍继续执行与该分支
无依赖关系的 ready 节点。首版仍为串行派发，但不会因为拓扑上无关的人工等待或失败而提前停止
整张图。

BuildRun 状态按以下优先级聚合：

1. 存在 `running` 节点，或仍有能够通过当前准入检查的 ready 节点：`running`。
2. 没有可执行节点，且存在 `recovery_blocked` stage 或 dispatch block：`recovery_blocked`。
3. 没有可执行节点，且至少一个节点为 `waiting_for_input`：`waiting_for_input`。
4. 所有节点均成功：`succeeded`。
5. 无可执行节点且存在 `interrupted`：`interrupted`。
6. 无可执行节点且存在 `failed`，其后代已标为 `blocked`：`failed`。

`blocked` 不单独成为整体终态；它的根因来自 failed、interrupted、无效证据或未满足的人工输入。
同一图同时存在 waiting 和 failed 时，只要人工响应仍可能解除阻塞，整体保持
`waiting_for_input`，同时保留失败节点事实；人工分支结束后再重新聚合。

`recovery_blocked` 的优先级高于 waiting/failed，因为此时 Core 不能证明恢复或派发安全。但若
图中仍有与阻塞原因无关、且能够通过准入检查的 ready 节点，Scheduler 先继续执行这些节点，整体
保持 `running`；这些工作耗尽后才聚合为 `recovery_blocked`。

两类首版阻塞的解除条件为：

- **evidence_invalid**：修复或重新导入证据后重新验证，或者用户显式重试该节点并创建新 attempt。
  原 succeeded attempt 不删除，其当前输出不得继续供下游使用。
- **compute_admission_blocked**：持久探测证明旧进程组已经退出，或操作员完成明确的进程处置并写入
  可验证终态。目标节点在此期间保持未派发，不创建虚假的 running attempt。

## 9. 扇出、汇合与失败传播

### 9.1 扇出

```text
A ──→ B
 └──→ C
```

- A 只执行一次。
- B、C 的 resolved input 保存同一 A output `ArtifactRef`。
- B 失败不删除或重写 A、C 的事实。

### 9.2 汇合

```text
B ──┐
    ├──→ D
C ──┘
```

- D 只有在 B、C 均成功、输出证据有效且关系校验通过后才 ready。
- 完成先后不影响端口绑定；按端口名和计划引用解析，不按事件顺序拼接。
- 任一依赖失败时 D 为 `blocked`，不得伪装为 `failed` 或启动 Backend。

### 9.3 多实例隔离

两个节点可以引用同一个 Operator，但必须分别记录 node ID、参数、Backend 和 provenance。缓存或恢复键不得只使用 Operator 名称，否则会串用实例结果。

## 10. 人工节点

人工节点是一等节点类型，使用与计算节点相同的输入绑定、attempt、output 和恢复模型。

流程为：

1. Adapter 根据实际输入创建不可变 `HumanInputRequest` Artifact。
2. 状态持久化为 `waiting_for_input`。
3. UI 读取请求并提交带 idempotency key 的决定命令。
4. Core 校验决定与 request、input digest、decision contract 和证据一致。
5. 决定本身持久化为 Artifact，并作为节点输出进入下游。

A2 CPU 首版先耐久保存 `prepared` 幂等回执，再创建内容寻址的决定 Artifact，最后保存 committed 回执及 attempt。发布前后崩溃均可根据 prepared 内容恢复同一决定；已提交命令的重放不触发执行，中断工作由显式 retry 创建新 attempt。revision 用于新命令 CAS，不属于决定内容身份。

前端布尔值、临时表单状态或 URL 参数不能替代决定 Artifact。重启后必须恢复原请求和已保存草稿；响应丢失时通过回执去重，不能重复确认或重复派发。

上游证据改变会使旧决定失效。同一运行恢复且输入 digest 未变时保留原决定；新运行即使候选 Artifact 相同，也只能提议显式复用，并记录原决定引用和用户确认。

## 11. 重试、失效与复用边界

### 11.1 基本规则

- 成功的历史运行不可变。
- 失败或中断节点在输入与计划未变时可以在同一运行创建新 attempt。
- 编辑参数、Backend、节点或边会创建新计划和新运行。
- 同一运行恢复时保留并重新验证已经成功的节点，不把它们作为新执行或缓存命中。
- 不因目录中存在文件、状态字段为 succeeded 或 child process 退出就认定结果有效。

### 11.2 输入摘要

节点 `input_digest` 至少包含：

- 实际 resolved input 的 Artifact/StructuredValue identity。
- 规范化参数。
- Operator、Adapter 和 Backend binding identity。
- relation validator 版本与摘要。
- 影响结果的 decision Artifact。

它不包含路径、URI、时间戳、run ID、child run ID、command receipt 或 UI 布局。

### 11.3 同一运行的重试与失效传播

失败或中断节点显式重试时：

- 已成功节点按计划重新解析输入，并验证 `resolved_inputs`、`input_digest`、依赖证据和输出证据。
- 验证通过的上游和无关旁支保持成功，不重新执行。
- 被重试节点创建新 attempt。
- 被重试节点的可达后代回到 `pending` 或按依赖状态成为 `blocked`；join 只有在所有输入分支重新形成有效绑定后才能运行。
- 如果已成功节点的输入绑定或摘要与计划重新解析结果不一致，即使输出 Artifact 完好，也必须拒绝
  恢复为当前成功：保留原 succeeded attempt，将当前 StageState 标为 `recovery_blocked`，记录
  `error_code=recovery_input_mismatch`，并将其后代标为 `blocked`。解除方式是证据修复后重新验证，
  或显式重试并创建新 attempt。
- 输出 Artifact、schema 或依赖证据损坏时使用相同模型，并记录对应的稳定 error code，例如
  `recovery_output_invalid` 或 `recovery_dependency_invalid`。

Scheduler 应基于依赖图计算受影响集合，禁止只重置拓扑列表中“后面的节点”。

### 11.4 跨运行复用延期

Milestone A 不实现普通计算节点的跨运行输出复用，也不实现新计划中的自动旁支缓存。新计划默认
产生新运行并重新执行其节点。人工决定只保留第 10 节已经定义的显式复用入口。

未来若增加跨运行复用，至少需要来源 attempt 查找、Backend 缓存/确定性策略、输出证据闭包验证、
本次运行的 `reused` attempt 和明确 provenance。在这些契约实现前，不得仅凭 `input_digest`
相同引用旧输出。

## 12. 恢复与进程边界

恢复时按以下顺序处理：

1. 获取该运行的单写者锁。
2. 加载 `CompiledPlan`、BuildRun 和最新耐久状态。
3. 验证 plan identity、state revision 和节点集合一致。
4. 从 Pipeline 实际输入和已验证的上游输出重新解析每个 succeeded 节点的输入，比较保存的
   `resolved_inputs` 和 `input_digest`，并验证依赖 Artifact 闭包。
5. 输入证据一致后，再校验该节点的输出 Artifact 闭包、schema 和身份。
6. 恢复 waiting 人工请求和幂等回执。
7. 对 running 节点检查 child registration、worker identity、进程组或远程 job 状态。
8. 对所有待派发计算节点执行跨运行 compute admission；旧运行的已授权进程仍活动或状态未知时
   阻止新派发。
9. 无法证明继续运行或已提交成功时标记 interrupted/unknown，并阻止重复启动，直到所有权被确认。
10. 重新计算 ready、blocked 和整体 BuildRun 状态。

步骤 4 或 5 失败时，不覆盖原 attempt；写入耐久 recovery block，将当前 stage 标为
`recovery_blocked`，其下游标为 `blocked`，并停止使用该输出。步骤 8 的 compute admission 失败时，
写入 `compute_admission_blocked` dispatch block；目标节点保持未派发。两种情况都必须通过服务 API
投影给 UI，不能让页面长期显示 `running`。

本地计算节点继续遵守已有 launcher 门控：Backend 获得执行放行前，进程身份必须耐久写入并 `fsync`。工作台服务崩溃后不得仅凭锁释放判断 GPU 子进程已经退出。

通用 Scheduler 首版不承诺接管一个只完成模型推理、尚未完成 Core 后处理的孤立子流程。需要跨服务崩溃继续完成整个节点时，节点应由独立 child execution process 承载完整 Adapter 生命周期；该扩展在 Milestone A 的恢复契约稳定后实施。

## 13. HTTP 与 ComfyUI 复合 Backend

HTTP Adapter 必须明确：

- 幂等 submission key。
- remote job ID 和查询接口。
- 上传、下载、超时和重连行为。
- 响应丢失后的 job lookup。
- 服务是否真实支持取消和恢复。
- 输入输出导入 Artifact Store 前的 digest、schema、frame/unit 和关系校验。

ComfyUI 作为不透明复合 Operator/Backend 接入，不作为 Core Scheduler。至少记录：

- 实际 workflow JSON 摘要。
- ComfyUI 版本。
- 可获取的 custom node、模型和权重 identity。
- 实际参数和 remote job ID。
- 输入与输出 Artifact 映射。

无法取得的内部身份标为 `unverified`。外层节点成功只证明边界调用和输出验收成功，不证明内部每个 ComfyUI 节点都有完整 provenance，也不自动授权跨运行缓存。

## 14. React Flow 边界

React Flow 只负责：

- 拖放节点、端口连线、选择和布局。
- 根据后端节点目录生成参数表单。
- 显示静态兼容提示、编译错误和实例级运行状态。
- 打开现有人工审查页面或组件。
- 保存 DraftGraph 和独立 UI layout。

节点目录由服务根据 `OperatorSpec`、AdapterRegistry 和 Backend descriptors 生成。前端不得维护第二套权威端口定义。

连接时前端可即时拒绝明显不兼容的 kind/schema/cardinality，但后端编译结果才是权威。关系 validator 需要真实 Artifact 才能判断时，UI 应显示“连接可编译，运行前仍需数据验证”，不能显示为已验证。

已执行图被编辑时创建新 Draft revision，重新编译后形成新 plan/run。画布布局变化不创建新执行计划。

Milestone A 前允许独立的纯前端技术 spike，但它不得触发真实模型、写生产 BuildRun 或维护另一份运行状态。

## 15. 实施里程碑

### 15.1 Milestone A1：编译计划

- 新增 `CompiledPlan`、`CompiledNode` 和规范化 binding schema。
- `compile_pipeline()` 返回计划并保持现有静态校验行为。
- 实现深层不可变结构和序列化往返。
- 固定稳定拓扑顺序和 plan digest。
- 引入 relation validator registry 与最小 validator 契约。

### 15.2 Milestone A2：通用 Scheduler

- 新增 AdapterRegistry；把节点实现与 stage ID 分离。
- 复用工作台状态迁移、持久写入和恢复机制。
- 先以纯 CPU/Fake adapters 跑通链、扇出、join、人工等待和失败恢复。
- 用专门菱形图验证调度语义。

### 15.3 Milestone A3：迁移真实 Pipeline

1. 迁移 `image_asset_v2`，保持产物身份、provenance、QA 和 BuildRun 行为可解释。
2. 运行现有真实单图 Backend smoke；不以 FakeBackend 替代。
3. 在不修改 Scheduler 的条件下迁移 `multi_view_asset_v1`。
4. 为相机、深度、图像和 frame lineage 实现关系 validator。

如果迁移第二张图需要在 Scheduler 中加入 Workflow 名称分支，Milestone A 不算完成。

### 15.4 Milestone B1：只读图与编辑

- 建立独立前端构建并由 Python 服务托管静态产物。
- 用 React Flow 渲染 DraftGraph、CompiledPlan 和运行状态。
- 支持拖放、连线、参数编辑、保存和加载。
- 后端返回可定位到 node/port/edge 的编译错误。

### 15.5 Milestone B2：生产执行接入

- 从编辑器创建不可变计划和新运行。
- 支持人工等待、重启恢复、显式重试和结果预览。
- 保持现有固定工作台入口，完成迁移和兼容验证后再决定是否替换。

### 15.6 Milestone C：远程复合 Backend

- 先用模拟 HTTP 服务验证幂等提交、job lookup、超时和响应丢失。
- 再接一个真实 HTTP Backend。
- 最后单独验证 ComfyUI workflow 封装及证据边界。

## 16. 验收矩阵

| 场景 | 必须证明的行为 |
| --- | --- |
| 确定性编译 | 相同输入产生相同 plan digest；UI 布局变化不影响 digest |
| 输入契约变化 | Pipeline input 的 kind/schema/cardinality 变化会改变 plan digest |
| 环和坏引用 | 编译失败，错误定位到节点与端口 |
| 菱形图 | A 只执行一次，B/C 获得同一 ArtifactRef，D 等待两者 |
| 分支失败 | D 标为 blocked，成功分支和 A 的结果保留 |
| join 关系错误 | relation validator 拒绝，Backend 未启动 |
| 多实例 | 相同 Operator 的参数、Backend、provenance 不串用 |
| 人工等待恢复 | 重启后恢复同一 request、草稿和决定证据 |
| 响应丢失 | 幂等回执阻止重复决定或重复派发 |
| 同运行重试 | 只创建失败节点的新 attempt，不重跑已验证旁支 |
| 输入绑定漂移 | 输出仍完好但 resolved input/input digest 不匹配时拒绝恢复成功 |
| 恢复证据损坏 | 保留原 succeeded attempt，当前 stage/run 进入 recovery_blocked，下游不执行 |
| 独立分支 | 一条分支等待或失败时，其他 ready 分支继续执行 |
| 状态聚合 | running、recovery_blocked、waiting、failed/interrupted 和 blocked 按 8.4 规则聚合 |
| 阻塞解除 | 证据重新验证或新 attempt 后解除 evidence block；进程退出证据解除 admission block |
| 跨运行孤儿 | 运行 A 的活动或未知 Backend 进程阻止运行 B 派发 GPU 计算，B 显示 recovery_blocked 而非 running |
| 新计划 | 修改参数或边后建立新运行，不自动复用旧计算节点输出 |
| 输出损坏 | succeeded 状态不能掩盖 digest/schema 验证失败 |
| 单图迁移 | 与现有 `image_asset_v2` 的发布和 QA 语义一致 |
| 多图迁移 | 不修改 Scheduler 即可运行，并验证相机/深度关系 |
| HTTP 重连 | 响应丢失后按 idempotency key/job ID 找回同一作业 |
| ComfyUI 边界 | 缺失内部身份显示为 unverified，不伪造完整 provenance |
| 前端权威性 | 篡改浏览器图或参数不能绕过后端重新编译 |

## 17. 开工前仍需确定的最小问题

以下问题应在 A1 的 schema PR 中定稿，不继续扩展为平台设计：

1. `CompiledPlan` 是扩展现有 `ResolvedPlan`，还是让 `ResolvedPlan` 成为其中的 Backend binding 子结构。不得长期保留两套重叠的 Pipeline identity。
2. relation validator 的声明放在 OperatorSpec YAML 中，还是由独立 registry 通过 Operator key 绑定。无论选择哪种，Operator 必须显式引用，版本和摘要必须进入计划。
3. 现有 `WorkbenchPlan` 和 `WorkbenchState` 的兼容读取策略。建议读取旧 schema，新增运行写通用 plan/state schema，不原地改写历史记录。
4. 单图 Workflow 中哪些步骤首轮作为独立节点，哪些暂时保留为复合 Adapter。选择以能够验证扇出、恢复和 provenance 为准，不为画布展示强行拆碎原子操作。

这些问题解决后即可开始 A1。自由连线的产品范围、自动布局、节点分组和分布式执行不阻塞开工。

## 18. 设计完成标准

本设计的首个工程完成点不是“画布能连线”，而是：

> 同一套后端编译和调度代码能够执行一张带扇出、汇合和人工等待的图；进程在人工等待或分支失败后重启，仍能证明已完成输出未变，只重试必要节点，并拒绝语义不成立的汇合。

该验收通过后，React Flow 才接入生产运行。这样画布展示的是实际执行事实，而不是与真实 Workflow 并行维护的演示状态。

## 实现补充：本地节点 Backend 绑定

编辑器本地 profile 目录映射为 `(backend_name, adapter_key)` 的受信实现。节点显式 `backend` 必须匹配兼容的注册项；省略时沿用默认 AdapterRegistry 项。BoundAdapter 仅在显式绑定时序列化 backend 名，参数 schema/defaults 固定该 profile 的身份摘要；provenance 的 adapter_identity 保留完整绑定。此目录不复制 Operator 端口，不接受浏览器提交可执行代码或模型路径。当前范围仅本地 profiles，远程服务按 Milestone C 单独实施。

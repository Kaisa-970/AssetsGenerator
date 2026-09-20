# 远程 Backend v1：作业协议与恢复边界

状态：耐久 HTTP 作业服务、DAG remote 调度、进程门控、可信启动配置目录和编辑器逐节点 Backend 选择已接入。真实 TRELLIS.2 shape 及 SAM 选区到发布链已有脚本 smoke；完整 Milestone C 尚未关闭。以下历史追加章节中的“尚未实现”仅适用于当时状态，当前状态以本节为准。

| 能力 | 当前状态 |
| --- | --- |
| 请求与服务身份、严格 JSON、有限 HTTP 传输 | 已实现，本机 HTTP 回归覆盖 |
| 输入 Blob 上传、固定成功结果下载 | 已实现；实际 RGBA/GLB 解码与契约校验，真实 TRELLIS.2 输出已导入 |
| 耐久提交日志、父 DAG attempt 归属 | 已接调度、固定结果与恢复；重试须核实旧作业状态 |
| remote Adapter、非终态调度、恢复与重试门控 | 已接编辑器运行入口；查询不自动派发，显式恢复继续核实结果 |
| 服务端耐久作业与进程门控 | SQLite、HTTP 重开、CPU worker 中断回归；显式执行/队列命令与进程退出核实 |
| 真实模型 | TRELLIS.2 shape 和 SAM→选区→发布链脚本 smoke；一次 CuMesh 后处理采用无纹理 fallback，不代表质量验收 |
| 服务目录与 UI | 启动时 `--remote-config` 注册可信 profiles，画布逐节点选择；固定部署参数随切换清除，后端重新绑定 |
| 本地分割与远程生成 | `--proposal-config` 只加载 SAM，无本地 Shape 依赖；真实 SAM 启动核验及 CPU 发布/恢复回归 |
| 双服务组合 | 两个 HTTP 服务的 CPU 独立作业、10 节点双发布与离线恢复；真实双模型尚未验收 |
| ComfyUI | 尚未实现；需独立定义复合 workflow 与内部证据边界 |

当前目录是启动配置，不是自动服务发现、在线注册或部署管理。新配置入口的真实浏览器
全链、远程真实模型推理中的服务异常退出仍需单独验收；CPU 测试不能替代这些证据。
验证与使用见 [服务指南](../guides/remote-shape-service.md)、
[真实 shape](../reports/remote-shape-real-smoke.md)、
[选区发布链](../reports/remote-selected-trellis-fallback-real-smoke.md) 和
[SAM-only 启动](../reports/sam-only-editor-startup.md)。

## 固定身份

服务配置固定 endpoint、service_id 与 backend_digest。endpoint 仅是传输位置，不作为模型身份；backend_digest 是部署代码、模型、环境和默认参数声明的规范化摘要。每次响应必须回传同一 service_id/backend_digest，变更时拒绝认领旧作业。认证凭据由服务器环境提供，不进入计划、日志或 Artifact。

提交键绑定父 run、节点实例、attempt；请求摘要绑定 Operator/Adapter/Backend、实际输入与规范化参数，不含 URL、时间戳或 job ID。Core 必须在联网前耐久记录提交键、请求摘要和目标身份。相同键同摘要只对应一个作业；同键异摘要返回 409。此映射由服务持久化，服务重启不可遗忘；无法满足此规则的服务不能声明可恢复。

## 最小 HTTP 接口

- `POST /v1/jobs`：JSON 包含 submission_key、request_digest、service_id、backend_digest、payload。成功返回 200/202 和 JobRecord。
- `GET /v1/jobs/by-key/<submission_key>`：返回相同 JobRecord；404 只代表本次未找到，不证明从未接收。只有服务真实支持原键幂等时才可重发同键，不能生成新键。
- `GET /v1/jobs/<job_id>`：查询已固定作业。job ID 只允许 URL 安全标识，不把服务任意返回字符串当下载 URL。

JobRecord 固定字段：protocol_version=`1`、service_id、backend_digest、submission_key、request_digest、job_id、state、result、error。state 是 queued/running/succeeded/failed；succeeded 必须有 result 且无 error，failed 必须有明确 error 且无 result，其余均无 result/error。未知状态/字段、身份不匹配或成功状态夹带错误一律拒绝。

result 是边界结果描述，不直接等同于 Core ArtifactRef。后续下载须验证声明摘要、格式、kind、schema、frame/unit 和关系，再导入本地 Store；只允许配置服务内的显式下载接口，不跟随任意 URL/重定向。服务端失败信息保留其 code/detail，Core 验证失败另行记录。

## 不确定状态与恢复

网络断开、超时、非协议响应、5xx、找不到已固定 job 都不能推导为模型 failed。记录为 transport unknown，保留原提交身份；恢复先 lookup，同 job ID 则继续查询，job ID 冲突则阻塞。客户端首版不自动重试 POST，不把超时当取消，不声称远程进程已退出。

DAG 集成前必须新增明确 remote execution kind 和耐久 remote job 记录；不得伪装 cpu/process 来绕过已有门控。远程作业活跃/未知时禁止为同 attempt 换键或创建重复推理；共享资源 admission 由明确的服务调度契约承担，本地 PGID 无法证明远程状态。查询成功之后仍需完整输出证据闭包，不能凭 HTTP 200 或 succeeded 字符串发布。

取消首版不提供；ComfyUI workflow 仍需记录 workflow、版本、自定义节点和模型身份，未验证的内部来源显式标记。

## 实施与验收顺序

1. 严格请求/响应身份和状态模型、真实 HTTP 传输测试（含响应丢失后 lookup）。
2. 耐久提交准备及 remote job ID 固定、崩溃窗口/冲突/超时恢复测试。
3. 显式 DAG remote kind、查询/恢复及输出导入，菱形分支故障与恢复。
4. 真实独立环境模型服务，通过同一协议做发布 smoke；再开放受信任服务目录与 UI。

第一步通过不等于远程 Backend 已接入，模拟作业不等于真实模型验收。

## 当前落地证据

`remote_protocol.py` 实现 RemoteIdentity、RemoteRequest 与严格 JobRecord 解析。请求仅保留规范化 JSON bytes，调用方修改原字典不影响身份；结果/error 也复制为 bytes，尚不导入 Artifact Store。15 项协议测试通过，覆盖键不进入内容摘要、身份串线、job ID 改变、未知状态、终态约束、错误码保留和非规范 JSON 拒绝。mypy（88 源码文件）与相关 Ruff 通过。

HTTP 客户端已在 remote_http.py 实现 submit/lookup/query，禁止自动重试和重定向，限制 JSON 响应体积。真实本机 HTTP 测试在服务已创建 job 后断开连接，验证 lookup 找回同一 job 且提交次数不增加；同键异摘要返回明确冲突，5xx/重定向/超大响应/读取超时/身份不符均为状态未知。已固定 job 查询 404 不当作终态，只有 lookup 404 返回未找到。服务明确 failed 保留原 error code/detail。

耐久提交准备、DAG remote kind、真实模型服务仍未实现。HTTP 客户端没有自动恢复持久化状态，也不能把模拟服务内存映射称为跨服务重启验收；下一步接耐久提交与 job ID 固定。认证、输入上传及输出下载也尚未接入；当前入口仅供受信配置的协议测试，未注册进生产 AdapterRegistry。

HTTP/协议合跑最终 26 项通过；相关 Ruff、mypy（89 源码文件）通过。本轮无真实模型/GPU 验收。

## 耐久提交日志首版

`RemoteSubmission` 使用已有 WorkbenchRepository 的独占锁、命令锁、DurableIO 与写入失败后 poisoned 语义。请求和 endpoint 先写为 prepared；发送 POST 前耐久写 authorized；确认响应后写 observed 与完整已验证 JobRecord。endpoint 暂固定，不隐式迁移服务位置。提交键同请求摘要但 endpoint 改变也拒绝复用。

恢复仅查询：没有固定 job ID 时按提交键 lookup，有 job ID 时 query；查询 404/网络错误不会生成新键或发送 POST。authorized 但查不到作业时保持不确定并阻止自动重发；这包含授权落盘后、实际发送前崩溃的保守窗口。显式同键重发策略及服务能力声明后续再接，当前不会通过新 attempt 绕过未知作业。

已观察终态的结果不可变，running 不允许退回 queued；服务返回另一 job ID、另一身份或更改终态时拒绝写入。结果仍是远程描述，未成为可发布 Artifact。本地 JSON 日志损坏会拒绝恢复，不重建请求。日志目录仍依赖上层保存其归属；DAG 集成必须把该提交记录与具体 attempt 固定，不能仅依赖一个可丢失的旁路文件。

验证：真实本机 HTTP 与协议、日志合跑 30 项通过。覆盖响应丢失后关闭/重开 Repository 找回同 job、授权后发送前中断、查询未找到不重发、job ID 变化、终态内容变化、请求冲突及写盘失败不联网。这里重开的是客户端仓库，模拟服务仍存活，不能称为服务重启持久化验收。DAG remote execution kind、输入/输出传输与真实模型仍未接入。

### 提交日志丢失与响应落盘窗口

新增 `remote_reservations` 独立不可覆盖记录，先于 prepared 日志落盘。现存日志每次读取必须匹配该记录；预留存在而日志缺失时拒绝重建，日志存在而预留缺失也拒绝。这修复了删除已提交日志后被误当作首次提交的问题。若预留后、prepared 写入前崩溃，首版同样保守阻塞，不假设缺失日志只是尚未创建；未来由固定到 DAG attempt 的证据提供明确恢复路径。

独立记录不能抵御两份记录同时丢失，因此它不替代 DAG 级不可变提交归属，生产 remote kind 仍未开放。已有试验日志无预留时拒绝读取，不自动迁移/补写来掩盖缺失证据。

补测日志缺失/损坏、预留缺失、预留后日志写入失败，以及服务已返回 job 但 observed 写入失败后重开客户端仓库恢复。合跑协议/HTTP/日志 35 项通过；Ruff 与 mypy（90 文件）通过。模拟服务保持存活，不声称服务端重启持久化已验收。

## DAG attempt 归属模型

新增不可变 `RemoteAttemptBinding`，记录 run/node/attempt、实际 input_digest、binding_digest、规范化 endpoint、服务/Backend 身份、完整规范化请求及摘要。submission_key 由 run/node/attempt 确定，不由远程服务或浏览器指定；请求 payload 必须包含对应输入与绑定摘要。此记录作为可选 DagAttempt.remote_binding 持久化，缺省字段从序列化省略，旧 attempt 身份形状保持不变。

模型层核对 node/attempt/input/backend，Repository 创建与保存核对父 run；一旦存在，连活动 attempt 也不能移除或替换绑定。已保存记录可往返读取，状态变为 interrupted 不移除绑定。当前 Scheduler 明确拒绝包含远程绑定的运行，直到 remote execution kind/查询恢复/输出导入完整接通；不能让其落入旧本地恢复路径。

测试：远程绑定/既有 DAG persistence/engine 合跑 50 项通过；随后新增调度拒绝且记录不变回归，绑定文件 9 项通过（两轮重叠不相加）。Ruff、mypy 通过。此步仅建立归属模型，RemoteSubmission 尚未自动由引擎注册/调用，远程执行尚不可用。

## DAG 与提交日志桥接

内部 `DagRemoteSubmission` 新增 prepare/submit/recover。prepare 在单写者/命令锁内先保存准备日志，再 CAS 保存当前 attempt.remote_binding；不联网。每次 submit/recover 都从 Store 重读父运行及目录所有权，核对当前 attempt、输入/Backend 摘要与完整绑定。首次 submit 仅允许 running owner；恢复只查询原请求，不改变 DAG 成功状态。

已固定 binding 的 prepare/submit 必须读取现有日志，不调用重建路径。因此即使 remote_submissions 与 remote_reservations 同时丢失，父 BuildRun 仍阻止重建后提交。父快照保存失败时网络未启动，重开客户端仓库后可继续完成原 prepared 绑定；内存中未保存的 binding 无法授权提交。

本轮桥接/日志/绑定合跑 21 项通过，随后新增父保存失败测试，桥接单文件 4 项通过；Ruff、mypy（92 源文件）通过。该桥接是内部持久化基础，尚未由 Scheduler 的受信 remote Adapter 调用；计划重编译、relation 检查、作业非终态调度及输出导入仍需接通，当前引擎仍拒绝执行含 remote binding 的运行。不得绕过该限制把桥接暴露为任意 HTTP 执行接口。

## 结果下载传输边界

成功 JobRecord 的首版结果格式为 `{"outputs": [{"output_id": "mesh", "blob_digest": "sha256:…", "byte_length": 123, "media_type": "model/gltf-binary"}]}`。描述符严格拒绝未知字段、重复 ID、非法标识和摘要；不允许服务提供任意 URL。GET `/v1/jobs/<job_id>/outputs/<output_id>` 返回原始字节；继续禁止 HTTP 重定向。

`RemoteJobClient.download()` 查询并核对请求/job 身份和成功状态，解析所有描述符，先检查配置上限，再最多读取声明长度加一字节；媒体类型、实际长度、SHA256 全部匹配才返回 bytes。默认单输出上限 128 MiB，可由受信调用方收紧。非终态、未知输出、任意 URL 字段或重复 ID 均在下载前拒绝。

这只验证传输字节，不信任媒体类型字符串等同于正确编码；未导入 Artifact Store，也不认领 kind、schema、frame/unit 或 provenance。未来 Adapter 必须对照耐久 observed 结果固定描述符，并做实际格式/关系校验；当前低层下载查询不是耐久终态证据的替代。输入上传、下载重连续传、流式落盘及远程 Scheduler 尚未完成。

本轮协议/HTTP/日志/DAG 桥接合跑 44 项通过；修正下载返回类型后 HTTP 文件再次 16 项通过。Ruff、mypy（92 源码文件）通过；无 GPU 或真实远程模型验收。

### 下载绑定到耐久终态

`RemoteSubmission.download` 只接受本地已观察并耐久保存的 succeeded JobRecord；不会在下载过程中认领新的成功结果。客户端重新查询得到的完整 JobRecord 必须与固定记录一致，之后才下载并核对原描述符。`DagRemoteSubmission.download` 再次验证磁盘上的父运行/attempt 所有权。因此服务后来同时修改文件和摘要，也无法被当成原结果接受；日志丢失时拒绝下载，不补建证据。

新增真实本机 HTTP 回归覆盖：仅服务端成功但未本地落盘时拒绝、客户端仓库重开后读取原成功输出、读取前后日志不变、服务换摘要时下载次数不增加、日志缺失时不下载。协议/HTTP/日志/桥接合跑 45 项通过，相关 Ruff 与 mypy 通过。仍仅返回验证后的 bytes，尚未执行 GLB/图像语义校验或创建 Artifact；远程 Scheduler 未开放。

## 输入 Blob 传输

客户端新增显式 `upload_blob`：PUT `/v1/blobs/<sha256 hex>`，body 为原始字节，Content-Type 为 application/octet-stream；通过 X-Service-Id/X-Backend-Digest 固定目标身份。发送前验证本地 SHA256、非空和配置大小限制（默认 128 MiB）。200/201 JSON 回执必须精确包含 protocol_version、service_id、backend_digest、blob_digest、byte_length，回执不匹配或网络异常属于上传状态未知。

上传不提交模型作业，客户端不自动重试。服务契约要求同摘要同字节重复 PUT 幂等、验证 body 摘要后保存，不得把 PUT 当推理触发器。提交 payload 未来只引用已验证的内容摘要及 Operator 所需的语义元数据；远程 Blob 不等同本地 ArtifactRef，frame/unit/schema 仍由边界校验。

本轮本机 HTTP 测试验证摘要错误/大小超限不联网、同内容重复请求地址稳定、上传不提交 job、回执摘要不符被拒绝；17 项 HTTP 测试及 Ruff/mypy 通过。模拟服务只验证请求行为，不代表已实现生产服务端 Blob 持久化或容量管理。尚未自动遍历输入证据上传，也尚未由 remote Scheduler 调用。

## HTTP JSON 严格解析

作业响应与上传回执共用严格 JSON 解码：拒绝任意层级的重复字段、NaN/Infinity 和溢出为无限值的数字（如 1e999），避免默认解析器静默保留最后一个同名字段。无效响应归为传输状态未知，不推断远程模型失败，也不自动重发提交。

协议、真实本机 HTTP、耐久日志及 DAG 桥接合跑 56 项通过；相关 Ruff 与 mypy（92 源文件）通过。此次修复不改变 DAG 的 remote 调度禁用状态，未做真实模型/GPU 验证。

## 下一实施切片：远程节点调度（待实现）

目标是一个真实本机 HTTP 的 CPU 菱形 DAG 验收，不先接 GPU、画布服务配置或任意 HTTP 算子。测试中的远程图像算子处理有效的小型 PNG；导入端实际解码、核对尺寸和媒体类型，再生成 Core Artifact。测试通过仅证明编排与恢复，不证明真实模型服务可用。

### 受信执行边界

- 新增明确的 `remote` execution kind；远程 Adapter 分离准备请求和导入结果，不通过普通 `execute()` 偷渡网络调用。服务 endpoint、身份和限制来自受信配置并固定到绑定中，浏览器不能提交任意 URL。
- 调度器先重新核对固定计划、实际输入闭包、端口及 relation，构造包含 Operator、实例、规范化参数与输入身份的请求。输入上传不触发推理；attempt 与请求归属耐久保存后，才允许提交。
- 不直接把整个 NodeExecutionContext 序列化到远端。输入只传明确选定的 Blob 摘要和必要语义元数据；本地 URI、运行目录和凭据不进入 payload。
- `DagRemoteSubmission.prepare()` 当前会自己保存父快照并增加 revision。引擎接入必须重载最新快照，避免随后用准备前的内存对象覆盖 binding 或触发过期 revision。

### 状态与恢复规则

远程状态必须与节点执行状态分开记录。活动节点可以保持 running，但需要耐久的 queued/running/transport_unknown 观察信息供 UI 展示。不能套用当前本地恢复逻辑，将所有 running 直接改为 interrupted。

| 观察或故障 | 调度动作 | 是否允许新 attempt |
| --- | --- | --- |
| queued / running | 固定原 job，等待显式恢复查询；继续无依赖的分支 | 否 |
| 超时、5xx、响应丢失或 lookup 未找到 | 保留原键及不确定状态；恢复只查询 | 否 |
| 提交日志缺失、身份冲突 | 持久化节点 recovery_blocked，不补建证据 | 否 |
| 服务明确 failed | 固定完整终态，保留服务 code/detail | 用户显式 retry 且重新验证后允许 |
| 服务 succeeded、尚未导入 | 保存成功终态后下载及语义校验；失败保留原 job | 否；先恢复同次结果导入 |
| 本地输出及 provenance 已固定 | 只读验证证据闭包，不重新下载或生成缺失 Blob | 无需 |

恢复不会发送首次或重复 POST，包括 prepared 后尚未授权的保守窗口。将来如开放“继续尚未授权提交”，需独立明确命令与测试，不能混入只读查询恢复。祖先重试重置后代之前，也必须检查受影响后代是否持有活动/未知 remote attempt；否则会绕过本节点重试门控产生重复作业。

### 结果证据与验收

当前旁路 JSON 日志不足以成为发布来源：调度器需要把成功 JobRecord、请求身份和输出描述持久化为不可变结构化证据并固定到 attempt；输出 provenance 引用该证据。已完成运行恢复以固定证据为准，不依赖服务在线或重新查询可变记录。传输失败、服务失败、结果语义错误和本地证据损坏必须能够区分。

必须覆盖：A 只执行一次；B 为远程图像节点、C 为独立本地分支；B 活跃/状态未知时 C 仍完成、D 等待；丢失提交响应后重开客户端仓库查询同 job 且 POST 次数不增加；明确失败保留错误码；活动 B 或活动远程后代禁止通过 retry 换 attempt；输出成功但父保存中断可恢复，已固定结果 Blob 丢失时只能阻塞，不能重建；终态身份变化被拒绝；整个运行成功后服务关闭仍可验证本地完整证据。

服务端跨重启耐久性另设验收：当前测试服务仅用内存保存作业，客户端仓库重开不等于服务重启验收。实现上述切片前不移除现有 fail-closed 调度限制。

### 不可变远程终态证据（已实现基础接口）

`DagRemoteSubmission.pin_result()` 将耐久 observed 日志中的终态固定为 `remote_job_result / RemoteJobResult@1.0` Artifact，内容为完整 attempt binding 和协议 JobRecord；引用存入可选 `DagAttempt.remote_result`。未观察或非终态不能固定，已固定引用不能改写或删除。字段缺省时不写入旧 attempt 的序列化形状。

`pinned_result()` 核验 Artifact 闭包、schema、完整归属及终态，仅读取本地 Store；服务不可用或旁路日志删除不影响已固定结果核验。重复 pin 首先验证原引用，损坏时拒绝，不重新生成 Blob。节点 provenance 工具新增可选 execution_evidence 父引用；这是供调度器使用的接口，尚未自动接入远程执行或结果导入。

本接口验证：DAG 桥接、绑定、持久化、引擎及 provenance 合跑 62 项通过；Ruff format/lint、mypy（92 源文件）通过。测试覆盖成功/失败终态、仓库重开、禁用网络并删除旁路日志后的本地核验、引用不可删除、Blob 丢失时不重建及 provenance 父引用。此次没有运行真实服务或 GPU，也未重新执行完整 Python 回归。

## 远程调度实验路径

新增 RemoteNodeAdapter 和明确 remote execution kind：受信注册要求 endpoint/service/backend 参数为单值 enum 并带相同默认值，禁止浏览器更换服务身份。请求构造与结果导入分离，不走普通 execute。引擎重新验证输入、关系和绑定；准备/固定终态后重新载入父快照，使用新 revision。queued/running/transport_unknown 保持原 attempt 并允许独立分支继续；明确服务失败保存原 code/detail。重试检查被重置的后代远程 attempt，未知或待导入结果不得换键重新提交。

成功输出 provenance 引用固定终态证据；成功恢复只核验本地证据。CPU 菱形测试使用真实本机 HTTP，覆盖正常等待和提交响应丢失两种路径、重开仓库完成同一作业、下游汇合、服务返回错误后的本地成功恢复、明确失败后显式重试及非法结果阻塞。相关 adapters/engine/bridge/binding 合跑 69 项通过；Ruff 与 mypy（94 文件）通过。

这仍是实验路径：没有自动上传输入 Blob，没有注册生产远程 Adapter，没有服务端持久化重启验证。测试算子返回 2×2 PNG，用于验证导入实际图像和编排，不能证明输入图像在远端经过模型处理。尚需补输入上传、结果导入崩溃窗口和复杂损坏恢复回归，再接真实服务。本文上方“待实现”状态表是本切片开始前的设计，不能将本次有限回归视为该表全部验收完成。

### 调度输入上传与损坏结果恢复

RemoteNodeAdapter.input_blobs 显式选择待上传的直接输入 ArtifactRef；Core 拒绝输入证据之外的引用，验证闭包并将 Artifact 身份放入保留的 input_blobs 请求字段。总输入大小限制 128 MiB，请求归属保存后、POST 作业前上传并核验回执。恢复只查询，不重新上传或提交；上传响应不确定后的 prepared 请求需要后续显式解决机制，当前不会自动绕过保守窗口。嵌套输入的选择与上传尚未开放。

修正已成功远程节点的损坏恢复：节点变为 recovery_blocked 但 attempt 保持 succeeded 时，仅走本地证据验证，禁止重新进入下载/导入路径。回归连续恢复两次，确认缺失输出不被重建且下载数不增加。

本轮 engine/adapter/remote bridge 合跑 62 项通过，Ruff lint/format（187 文件）及 mypy（94 源文件）通过。新增上传回执失败时不提交、恢复不重传，以及已完成输出损坏不重建测试。无真实模型/GPU 验证；未声称服务端已持久化上传内容或已实际消费输入。

### 实际输入变换与导入中断测试

本机 HTTP fixture 已能按 payload.input_blobs.image 中的 Blob 摘要读取先前 PUT 的图像，实际解码并反色为 PNG。验收输入红色 2×2 图像，Core 导入后回读像素为青色，输出 Artifact 不同于输入；输出 provenance 引用原固定远程终态。该 fixture 是内存服务，仅用于 CPU 协议验收，不是生产模型服务。

在导入前、导入完成但父成功快照保存前分别注入 BaseException 模拟进程中断；关闭并重开客户端 Repository 后完成同一个 job/attempt，POST 和上传均只有一次。后一个窗口允许重新执行尚未固定到父快照的确定性导入；已经固定的成功输出损坏仍由前一轮回归保证只阻塞、不重建。此次中断由测试注入，不声称执行了真实 SIGKILL。

验证记录：远程执行/HTTP/日志/桥接/绑定/既有引擎合跑 78 项通过；fixture 增加终态不重复处理条件后，远程执行单文件再次 8 项通过。两轮重叠不相加。Ruff lint/format、mypy（94 源文件）通过；无真实模型/GPU 或服务端重启验收。

## 服务端耐久存储基础

新增内部 RemoteServiceStore，使用 SQLite WAL 与 synchronous=FULL 保存固定服务身份、提交键/完整请求/JobRecord、按摘要校验的 Blob。提交以事务处理，同键同请求返回原作业，同键异请求拒绝；queued→running 使用事务内状态比较，同库多个连接只能认领一次。running→succeeded/failed 后不可再次改写终态。无效终态验证失败会回滚，仍保持 running。

重开数据库不重置 running，不自动执行排队或运行中作业；服务监督器仍需明确处理进程身份与中断。此层不提供 HTTP、认证、容量管理或模型执行，成功结果描述尚未绑定服务端输出 Blob，不能单凭该存储层宣称远程模型服务可用。当前数据库重开测试也不等同真实服务 SIGKILL 或掉电验收。

本轮服务存储、协议和 HTTP 合跑 45 项通过；Ruff lint/format（189 文件）、mypy（95 源文件）通过。包含数据库重开、双连接并发认领、同键冲突、服务身份变化、无效终态事务回滚和 Blob 摘要拒绝。无 GPU 或真实远程模型验证。

### 服务端成功发布与下载校验

RemoteServiceStore 在 running→succeeded 的事务内解析完整 outputs 描述列表，逐项核对服务端已保存 Blob 的摘要和长度；缺失 Blob、长度不符、重复 output_id 或任意额外 URL 字段均拒绝，事务回滚为 running。download 只按固定成功描述读取，并再次验证 Blob，不接受调用方提供存储路径。空 outputs 保留为协议允许的空结果；具体 Operator 是否允许由导入契约决定。

服务存储/协议合跑 29 项通过，覆盖数据库重开后下载、发布拒绝回滚及数据库内容损坏后的下载拒绝；Ruff/mypy 通过。此层仅保证传输内容，不将 MIME 字符串当格式验证，图像/GLB 语义仍由 Core 导入端处理。HTTP 服务及真实模型仍未接入。

## 本机耐久 HTTP 服务入口

新增 create_remote_server，固定监听 127.0.0.1，提供 Blob PUT、job POST、按提交键/job ID 查询和按固定描述下载。请求体有大小及读取超时限制，严格解析 JSON 和请求身份，拒绝 Origin 请求；暂无认证，因此不提供公网绑定或生产部署入口。POST 仅登记 queued，不自动调用模型或重放 running 作业。

真实本机 HTTP 测试关闭监听器和数据库后，使用原端口重开，核对同 job ID 与 running 状态；固定成功输出后再重开并通过既有客户端下载核验。重启是同进程中关闭/重新实例化服务，不是子进程 SIGKILL，不能证明 worker 中断恢复或掉电耐久性。服务/存储合跑 9 项通过，Ruff lint/format（191 文件）、mypy（96 源文件）通过。下一步接明确 worker 执行与进程级中断测试，真实模型仍未验收。

## 显式服务 worker 首片

execute_service_job 在调用受信 handler 前事务式认领 queued→running。handler 返回显式 bytes/media_type 映射，Core 验证描述与总大小、保存所有 Blob 后才发布 succeeded；普通 handler 异常固定为 SERVICE_HANDLER_FAILED，存储写入异常向上抛出，不伪装为模型失败。BaseException/进程终止不自动修改 running 或重新执行。

实际耐久 HTTP 服务验收已覆盖上传 PNG→显式 CPU 反色 handler→发布→关闭重开服务→下载并核对像素；重复执行同作业拒绝。该 worker 是同步受信调用基础，没有 GPU 环境启动、进程组监督或自动队列调度。真实模型仍必须走独立环境，不能把模型代码装入 Pipeline Core。

服务 worker/HTTP/存储合跑 12 项通过；含 spawn 子进程认领后经 Pipe 明确通知父测试，再由测试对该自建进程发送 SIGKILL，确认负退出码，重开数据库后仍为 running 且不能重认领。该测试证明保守不重放，不证明能自动判断或恢复任意真实模型的孤儿进程。Ruff、mypy（97 源文件）通过，无 GPU 验证。

## DAG 与耐久服务联调

新增 test_dag_remote_service：既有菱形 DAG 的 B 连接真实 RemoteServiceStore/create_remote_server/execute_service_job，上传红色 PNG，worker 从服务数据库取输入并反色，Core 实际导入青色图像。A/C/D 为本地测试算子，B queued 时 C 完成而 D 等待。

先关闭重开 HTTP 服务、数据库与 Core Repository，再执行原 queued job；成功已提交但 Core 尚未观察时再次重开服务。完成后所有节点各一次 attempt，handler 调用一次，固定 remote_result 闭包有效；最终关闭 HTTP 监听器，Core 离线 recover 的 node_states 与完成时一致。这里服务重开仍在同一测试进程，worker SIGKILL 由独立测试覆盖，两者不是一次完整多进程端到端 SIGKILL 验收。

DAG/服务 worker/HTTP/存储合跑 13 项通过，Ruff lint/format（194 文件）、mypy（97 源文件）通过。本轮没有新增生产模型 Adapter 或 CLI，不宣称真实模型可用。

## 真实模型服务接入边界（下一步）

现有同步 ServiceHandler 只完成 CPU 调用和输出持久化，不足以直接承载独立模型进程。优先复用 gated_worker.run_gated_process 的 prepared/identified/authorized/exited 回调，将对应 WorkerExecution 耐久绑定到服务 job；不能仅调用 LocalProcessWorker 后将返回码当完整进程退出证据。

服务端模型启动必须依次：验证固定模型配置及请求→事务认领 job→保存启动摘要→记录 host/boot/PID/PGID/starttime→保存 release 授权→释放 launcher。任何保存失败都禁止继续启动。服务重开只重新探测原身份，不重新启动原 running 作业；进程组仍活跃或身份不明时，禁止另一作业进入同一 GPU 配置。不能仅凭 HTTP worker 被终止就认为模型进程已退出。

首次模型服务选择现有已验证独立环境，不下载模型或 PyTorch。模型代码/权重/环境身份由已有 profile 身份工具计算并固定，上传输入按明确格式/尺寸/schema 验证。GLB 输出还须携带并核验 BackendNativeFrame、frame/unit、材质和关系，CPU PNG 反色验收不替代这些规则。服务 handler 捕获 PipelineError 时应保留其 error code；无法确认进程退出的超时不能直接变成可重试的终态失败。

验收至少覆盖启动授权前崩溃不执行、授权后服务退出不重复启动、孤儿进程阻止新 GPU 作业、原组退出后显式处理、同一次远程 shape 输出导入与 provenance，以及完整单图发布。自动排队、服务目录 UI、公网认证和 ComfyUI 后续另行实现。

## 服务端进程门控基础接口

ServiceProcessWorker 复用 run_gated_process，将 prepared、identity_recorded、release_authorized、exit_observed 的 WorkerExecution 写入服务 SQLite。每次回调以旧记录 bytes 作 CAS，固定作业归属、启动摘要与进程身份；同作业只能登记一次进程，不能重放。事务内检查整个服务数据库的未退出记录，当前同一数据库串行占用一个进程槽。

授权写入失败时 launcher 不释放命令；不完整记录保守保留占用，数据库重开不会自动清除。尚未接重新探测、显式释放占用或真实模型 handler，因此不要将其包装进现有通用 execute_service_job 后声称超时/孤儿进程可重试：后者目前会将普通异常记为失败，模型服务需先按退出证据区分不确定状态。此接口也不提供不同数据库之间的 GPU 互斥。

服务进程/存储合跑 10 项通过：真实独立 Python 命令及退出记录、同作业拒绝重放、授权保存失败不执行、重开数据库后未退出记录阻止另一个作业。Ruff 和 mypy（98 源文件）通过，无真实模型/GPU 验证。

### 显式重新探测进程占用

RemoteServiceStore.observe_worker 使用 LinuxProcessProbe 重新观察已保存的完整进程身份，确认 exited 后单独持久化 process_exits（绑定原 WorkerExecution bytes 和完整观察）。不改写原启动阶段、不编造 exit_code、不改变原作业状态，也不授权原作业重放。新作业准入可使用该固定退出证据释放同数据库进程槽；alive/unknown 保持占用。没有已记录身份的 prepared 窗口仍保守阻塞，尚无显式处置入口。

服务进程/存储合跑 12 项通过：实际授权失败后 launcher 退出的重新探测允许新作业执行；模拟 alive/unknown 均不写退出记录且仍阻塞。Ruff/mypy 通过，无真实模型/GPU 验证。尚未提供用户命令，也未将这些规则接入模型 handler 的终态分类。

### 进程证据约束服务终态

服务数据库在 running→succeeded/failed 事务中检查已登记 WorkerExecution：需要 exit_observed 且观察结果为 exited，或绑定原记录的独立 process_exits 证据。缺失退出确认时拒绝终态并回滚为 running，不允许通过普通 handler 异常把不确定模型进程变成可重试失败。没有登记进程的 CPU handler 保持原行为；受信模型 handler 必须使用 ServiceProcessWorker，不能私自启动未登记进程。

execute_service_job 对 PipelineError 保留原 code（如 backend_failed/backend_timeout），其他 handler 异常仍为 SERVICE_HANDLER_FAILED。实际独立 Python 非零退出验证错误码和退出码；额外覆盖成功/失败两种未确认进程终态均被拒绝。服务进程/worker/存储/DAG 耐久服务合跑 19 项通过，Ruff/mypy（98 文件）通过。仍未接真实模型或用户恢复命令。

### 服务成功必须具有零退出码

补强进程终态约束：登记过模型进程的作业发布 succeeded 必须具有原 worker 的 exit_observed、exited 观察及 exit_code=0。仅重新探测确认进程组消失可以释放占用并允许明确失败处置，但不足以证明推理成功。handler 即使吞掉非零退出异常并返回输出，也不能绕过数据库成功门控。

服务进程/worker/存储合跑 19 项通过，随后补充“重新探测退出但无退出码也拒绝成功”的断言；Ruff/mypy 通过。尚无真实模型/GPU 验证。

## Shape 服务输入导入基础

import_shape_rgba 按现有 prepare_observation 的 rgba_image/png@1.0 契约读取唯一 rgba 上传描述：核对完整 Artifact 身份摘要、Blob 摘要、RGBA 元数据，再实际解码有界 PNG 并拒绝空前景。导入服务侧 LocalArtifactStore 后必须保持原 Artifact ID，不接受请求中的本地路径。该函数尚未注册为生产 handler，不启动模型。

三项图像回归覆盖合法 RGBA 精确身份往返、全透明输入拒绝、以 RGBA 元数据伪装 RGB 编码拒绝；Ruff/mypy（99 文件）通过。下一步接固定模型配置与 shape 输出封装，未做 GPU 验证。

## Shape 输出传输首片

新增 export_shape_output/import_shape_output，将现有 ShapeOutput 封装为 mesh GLB 和 shape_metadata JSON 两个输出。导入核对 mesh 摘要与契约、NativeFrame/mesh frame-unit 一致性、材质颜色与 alpha、实际 GLB 有限非空几何，随后持久化 mesh；原 mesh Artifact 身份可往返保持。当前独立材质纹理引用拒绝，不能带着远端 ArtifactRef 直接进入本地 Store；GLB 内嵌外观仍随原字节保存。

此模块未注册进 DAG 或模型服务 handler；后续还需自包含 GLB 资源边界、Backend 身份与响应一致性校验，以及真实模型输出验收。输入/输出测试合跑覆盖小型 GLB 往返、frame 冲突、摘要损坏、非法颜色和悬空纹理引用；不宣称真实模型接入已完成。

本轮输入/输出合跑 8 项通过，Ruff 与 mypy（100 源文件）通过，无真实模型/GPU 验证。

### GLB 自包含边界

远程 shape 导入在调用 trimesh 前检查 GLB 2.0 header、精确总长度、对齐 chunk 和唯一 JSON/可选 BIN 顺序，严格解析 JSON，拒绝任意层级 URI（含相对路径、HTTP 和 data URI）以及 required extensions。首版仅接受已嵌入 BIN 的资源，避免解析器隐式读取外部文件；扩展支持须单独增加验收。输入/输出合跑 12 项通过，含资源 URI 与截断/长度不符回归；Ruff/mypy 通过。仍未接真实模型 handler。

## Shape 服务 handler 组合接口

ShapeServiceHandler 接受受信 Backend factory 和部署身份 verifier，客户端只可传 shape_generation@1、唯一 rgba 输入、固定摘要以及 seed/pipeline_type；拒绝模型路径等额外字段。导入后给 factory 注入 ServiceProcessWorker，调用现有 ShapeBackend.generate，推理前后重新核对部署身份，要求已登记且零退出的进程证据，再导出 mesh 与 shape_metadata。临时服务侧 ArtifactStore 不进入发布包。

本轮组合测试使用 CPU 测试 Backend（执行独立 Python 命令并返回小型 GLB），验证进程登记、两次身份检查、结果下载、临时目录清理和额外模型路径拒绝。shape handler/input/output 合跑 13 项通过，Ruff/mypy（101 文件）通过。尚未提供真实 profile factory、DAG shape Adapter 或启动 CLI；测试不证明任何真实模型可用。

## DAG RemoteShapeAdapter

新增受信 RemoteShapeAdapter，绑定既有 shape_generation@1（端口来自 operators YAML），固定服务 endpoint/identity，并允许节点配置 seed/pipeline_type。image Artifact 以 rgba 上传，返回值要求 mesh/shape_metadata 的媒体类型与内容校验，再输出原 Operator 的 mesh/material/native_frame。未修改 OperatorSpec 或复制端口定义。

DAG→耐久 HTTP 服务→ShapeServiceHandler→门控 CPU 测试 Backend→shape 导入已通过，成功后关闭服务仍能本地恢复，attempt 保持一次。该测试使用实际 YAML 合同，修正测试输入为显式 artifact_ref carrier 后，Adapter/handler/output 合跑 11 项通过；Ruff/mypy（102 文件）通过。尚未注册到生产编辑器目录，未运行真实模型。

## 现有真实 profile 的服务 factory

shape_handler_from_profile 仅接受非 test_only 且带 identity_check 的已加载 profile，从既有 ResolvedPlan 取 generate_shape，限制为 TripoSR/TRELLIS2。RemoteIdentity.backend_digest 绑定 shape_identity、Operator 和 Backend 版本；复用 loader 的模型/代码/环境资源 guard，并检查 Backend 配置未变。factory 复制 Backend 后注入 ServiceProcessWorker，不改变原 profile worker。

目前入口接收已加载 BackendProfile，未提供独立 shape-only 配置加载或 CLI；现有 workbench loader 仍同时核验 SAM，后续用户启动入口应避免无关模型依赖。profile/factory/DAG shape 合跑 25 项通过，测试使用模拟环境身份，不是实际模型资源验收。Ruff/mypy（103 文件）通过，无 GPU 推理。

## Shape-only profile 加载

workbench_profiles.load_shape_profiles 接受仅含 profiles 的配置，返回 ShapeProfile（无 proposer/SAM 身份）。从原 workbench loader 提取共用 shape 构造与资源核验，原 load_profiles 继续组合 SAM 和 shape，避免复制模型参数/摘要实现。shape_handler_from_profile 同时接受两种已验证 profile。

profile/原 workbench loader/DAG shape 合跑 26 项通过；新增测试将 SAM 构造器替换为必定抛错的函数，验证 shape-only 路径不调用它，并拒绝混入 sam 配置。Ruff/mypy（103 文件）通过。下一步提供服务启动/显式执行命令与真实资源验收，未运行 GPU。

## 独立命令入口

新增 python -m assets_generator.remote_shape_cli：serve 仅监听登记/查询，execute 显式执行指定 queued 作业，inspect 只读结果，observe 重新探测进程。全部使用 shape-only profile，并固定数据库服务身份；操作说明见 ../guides/remote-shape-service.md。命令/profile 合跑 5 项通过，Ruff/mypy（104 文件）通过；CLI 测试替换 profile/handler，不证明真实模型可用。尚未进行真实资源启动或 GPU 验收。

## 首次真实模型结果

TRELLIS.2 已完成单个远程 shape DAG 节点的真实 GPU smoke，含完成后关闭 HTTP 服务的离线恢复。详见[报告](../reports/remote-shape-real-smoke.md)。该结果不关闭完整发布、编辑器目录、自动队列或推理中真实模型中断验收。

### 模型响应身份核对

真实 profile factory 为 ShapeServiceHandler 注入输出身份校验：Backend 响应的 backend 和 model_digest 必须匹配加载时核验的 shape_identity。资源前后检查不能替代响应核对；响应缺失或串到其他模型时，禁止输出封装和成功发布。CPU 通用 handler 可不配置此钩子，真实 profile 路径强制提供。

profile/handler/DAG shape 合跑 6 项通过，覆盖缺失身份、Backend 不符和模型摘要不符；Ruff/mypy（104 文件）通过。本轮只读取既有真实 smoke 响应，没有重新推理，不将新增校验声称为已重新做 GPU 验收。

## 远程 shape 后续坐标转换

新增 CanonicalizeAdapter 调用既有 canonicalize_glb，固定规则版本、阈值、tie-break、原点/尺度规则及 spatial/operators 源码摘要到 Adapter 参数身份。当前只支持空 components，不开放组件映射变换。canonicalize Operator 增加 native_mesh_frame relation，验证 mesh 元数据与 NativeFrame 一致；同时绑定非空 components 的复杂汇合仍未开放。仓库与打包 YAML 同步更新。

修正 DAG 未绑定 zero_or_more/many 端口的执行缺省：依据 OperatorSpec 生成空列表，不把缺省 None 送入集合端口。远程 shape 联调现在继续运行 canonicalize，并校验 +Z 输出及 provenance 中的固定规则参数。相关远程 shape/relations/spatial/engine/workflow/compiled plan 合跑 136 项通过，Ruff/mypy（105 文件）通过；本轮未重新运行真实模型。修改 Operator 契约会使旧固定计划拒绝按新契约重新绑定，未提供历史计划迁移；此前真实 smoke 报告仍对应原代码基线。


### 通用 DAG 的几何 QA 接口

远程 shape 后可串接 `canonicalize_shape@1` 和 `geometry_validation@1` Adapter。
后者使用新增的同名 Operator，显式输入 canonical `mesh` 和原始 `source_mesh`，
通过 `canonical_mesh_source@1` 关系检查核对规范化来源。执行时进一步要求
canonicalization provenance 属于当前 BuildRun；节点实例名称不限定为 `canonicalize`。
旧 workflow 的 `validation@1` 及默认实例名检查保持原有契约。

报告复用 `geometry-v1`，持久化为 `QualityReport` Artifact，覆盖可加载性、非空有限几何、
digest、空间声明和来源记录。relative scale / estimated forward 保持 warn；
render-back 和 collision 未执行，不推断外观质量。QA 节点成功表示报告已生成，
下游发布仍须检查 `overall_status`，不能将节点成功等同于 QA 通过。

CPU HTTP 回归覆盖远程 shape → 任意名称的 canonicalization → QA 及服务关闭后的恢复，
并检查跨运行来源不通过、错误 source mesh 拒绝。此改动未重新执行真实 GPU 推理，
也尚未把 assemble/export 接入这条模块化测试链。Operator 与关系实现摘要发生改变，
旧固定计划不作隐式迁移。

### 单资产导出节点

`asset_export@1` Operator / Adapter 只接收一个 `AssetDefinition@1.0` Artifact，
从资产内读取 mesh 和 QA 身份，输出 GLB 与持久化 `AssetRelease`。
它要求一个 canonical +Z/+X visual mesh、匹配的 frame/unit、geometry-v1 的
mandatory passing checks，以及 QA provenance 指向同一 mesh；fail 不发布。
`appearance_mode=preserve_mesh` 固定进入计划和 provenance，保留网格内嵌外观。
独立纹理引用、附加几何及 physics 暂不支持，避免导出时静默丢弃。
这一步提供 Store 内 Release，不自动物化目录，不包含资产组装节点或完整远程发布验收。

### 模块化远程资产 YAML

`pipelines/remote_shape_asset_v1.yaml` 串接五个独立节点：remote shape、canonicalization、
geometry QA、shape asset assembly、asset export。通过可信 AdapterRegistry 注册
RemoteShapeAdapter（固定 endpoint/service/backend 身份）和四个 Core Adapter 后编译执行。
服务端作业仍需显式执行；本 YAML 不增加后台队列或浏览器服务配置入口。

`shape_asset_assembly@1` 的 `shape_asset_inputs@1` 关系校验明确关联 image、mesh、
quality 和 spatial：检查同运行生成→规范化→QA provenance 链，核对 frame/unit/方向和
实际顶点包围盒。Adapter 执行进一步限定为当前 BuildRun。材质摘要从 mesh 读取，
后续 export 保留 mesh 原有外观；不将单独材质覆盖到 mesh。资产 ID 绑定运行、节点和 attempt。
本入口接收 prepared RGBA，无 ObservationBundle，因此 source_observation_ids 留空，
不伪造观测 ID；输入 RGBA Artifact 在组装 provenance 的 derived_from 中可追查。

CPU 测试使用真实本地 HTTP/SQLite 服务与独立 Python 测试 Backend，验证五节点各一次
attempt、GLB 可回读、Release 证据闭包以及服务关闭后的恢复；拒绝另一图片、另一运行或
错误 bounds。该证据不替代真实模型全链验收。此前真实 smoke 只覆盖 remote shape 边界。

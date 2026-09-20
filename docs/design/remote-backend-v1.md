# 远程 Backend v1：作业协议与恢复边界

状态：协议、HTTP 上传/下载、耐久提交日志和 DAG attempt 归属桥接已实现；DAG 已有受信注册的 remote 调度实验路径及 CPU HTTP 回归，已接显式输入上传，尚无生产服务目录或真实模型验收。对应通用编排设计 Milestone C。以下“当前落地证据”及其后各节是按提交追加的历史记录，其中“尚未实现”描述只适用于该节当时状态；以本节和下表为当前状态。

| 能力 | 当前状态 |
| --- | --- |
| 请求与服务身份、严格 JSON、有限 HTTP 传输 | 已实现，本机 HTTP 回归覆盖 |
| 输入 Blob 上传、固定成功结果下载 | 已实现传输层；尚无 Operator 语义导入 |
| 耐久提交日志、父 DAG attempt 归属 | 已实现内部桥接；不能直接作为公开执行入口 |
| remote Adapter、非终态调度、恢复与重试门控 | 实验路径已实现并经 CPU HTTP 回归；尚未接生产目录或 UI |
| 服务端跨重启耐久作业、真实模型、服务目录与 UI | 尚未实现 |

首批使用模拟 HTTP 服务验证，再接真实独立模型服务，ComfyUI 另行封装。

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

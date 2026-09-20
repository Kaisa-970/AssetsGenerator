# 远程 Backend v1：作业协议与恢复边界

状态：协议与客户端基础实施中，尚未接入 DAG 或宣称真实模型可用。对应通用编排设计 Milestone C；首批使用模拟 HTTP 服务验证，再接真实独立模型服务，ComfyUI 另行封装。

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

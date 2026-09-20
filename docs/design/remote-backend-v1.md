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

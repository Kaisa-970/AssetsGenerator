# ComfyUI 复合 Backend 首版边界

状态：接入设计，尚未实现 Adapter 或真实服务验收。目标是将可配置的 ComfyUI
workflow 作为单个复合节点连接到 Core DAG，复用 Artifact、作业身份和发布校验，
不要求用户把内部每个 ComfyUI 节点重新编写成 Core Operator。

## 上游核实

2026-09-21 查阅固定 revision
[`0f74f7fb9f83a78bf46188fd4fd53e6bc44c1ae8`](https://github.com/Comfy-Org/ComfyUI/tree/0f74f7fb9f83a78bf46188fd4fd53e6bc44c1ae8)：

- [server.py](https://github.com/Comfy-Org/ComfyUI/blob/0f74f7fb9f83a78bf46188fd4fd53e6bc44c1ae8/server.py)
  的 POST /prompt 接受可选客户端 UUID prompt_id，经验证后调用 prompt_queue.put。
- [execution.py](https://github.com/Comfy-Org/ComfyUI/blob/0f74f7fb9f83a78bf46188fd4fd53e6bc44c1ae8/execution.py)
  的 PromptQueue.put 直接 heapq.heappush，没有按 prompt_id 检查重复请求。
- 服务提供 /queue、/history/{prompt_id} 查询，但查询不到不能证明从未执行；
  内存历史清理或进程重启都可能丢失观察证据。

以上仅证明该固定 revision 的代码行为，不保证其他部署版本。首版必须核验所接
版本，不能将 client_id、固定 prompt_id 或 HTTP 200 当作耐久幂等承诺。

## 最小实现路线

以现有 RemoteServiceStore 作外层耐久作业边界，新增专用 ComfyUI 服务 handler
及内部提交记录，不让浏览器任意指定地址、节点代码或服务器文件路径。
首个复合节点处理一种明确 Operator 契约；先验证图像输入输出边界，再扩展到
拥有明确 native frame/material 契约的 shape workflow。不能把任意图片输出
包装成 shape_generation。OperatorSpec 继续唯一声明端口。

受信启动 profile 包括 API workflow JSON、允许覆盖的节点输入及参数 schema、
输出节点与输出槽位映射、部署身份清单。实际参数替换后保存 submitted workflow
规范化摘要；同时保留模板摘要和映射版本。不能仅摘要编辑器 workflow 布局 JSON。
实际提交的 API 图须验证 node ID、class_type、引用边和映射目标；有歧义的输出拒绝。

## 不确定提交与恢复

外层 submission_key 仍有耐久唯一性。内部在 POST 前写 prepared 记录，固定
prompt UUID、实际 workflow、输入 Artifact/上传内容摘要和服务身份；prepared
持久化失败时不得联网。标记 sending 后才允许首次 POST，禁止 HTTP 自动重试。

若响应丢失或进程在 sending 后退出：只查询原 prompt UUID 和可验证的请求关联，
不自动 POST。匹配的 queued/running 继续等待；完整成功历史进入输出校验；
未找到、关联冲突或上游重启后历史丢失保持 unknown 并阻塞重试。固定 UUID
没有改变这一规则。不要调用全局 interrupt 来取消不属于当前作业的工作。

收到成功结果后，先耐久记录精确输出引用和摘要，再导入本地 Artifact。
ComfyUI 历史、输出文件后续丢失时，已经固定且校验通过的本地结果可离线恢复；
没有固定的结果不能从“同名文件”重建。不能复用本地 ProcessWorker 退出证据
假装远程 ComfyUI GPU 进程已退出；需独立的远程观察/不确定状态模型。

## 输出与来源

下载只允许 profile 指定服务内的显式接口，禁止任意 URL、重定向及路径逃逸。
校验尺寸、编码、kind、schema 和数据关系后导入。声明为 shape 的结果还必须
提供 native frame、unit、材质和几何校验；输出节点成功不等于资产可发布。

provenance 记录实际 workflow 摘要、部署 ComfyUI revision、custom node 身份、
模型/权重摘要、实际参数、prompt UUID、外层 job identity、输入输出 Artifact 映射。
缺少内部证据的项明确标为 unverified，标明 provenance 覆盖仅到复合节点边界；
不得因此拒绝所有可用复合节点，也不得伪造内部完整链或自动启用跨运行缓存。

## 验收顺序

1. CPU 协议服务验证准备日志、响应丢失、固定 UUID 重复入队风险、历史清理、
   服务重启未知状态、输出下载与内容身份；断言不确定时没有第二次 POST。
2. 一个小型真实 ComfyUI workflow 验证 API 图参数替换、上传下载、来源清单和
   Core Artifact 边界。复用已有环境；当前未发现现成部署，不自动安装模型。
3. 将复合节点接入 registry/画布，用同一 Operator 的本地与服务节点比较；
   最后验证真实 shape workflow 和发布。没有真实验收前保持实验性状态。

## 首个实现：单次提交日志

已新增独立 SQLite ComfySubmissionJournal。它将外层键、部署声明、实际 API 图和
预分配 UUID 固定，并在调用传输前耐久标为 sending。不同连接不能重复领取；
响应丢失、KeyboardInterrupt 或错误 prompt_id 后重开仍禁止再次 POST。
acknowledged 只代表收到对应提交回执，不代表 workflow 成功。

5 项 CPU 单元回归通过，Ruff/mypy 通过。当前未接 HTTP、队列/历史查询、输出
导入或 DAG；调用者还须保证部署内容已验证、HTTP 无自动重试，并将日志归属
固定在外层作业。数据库丢失后的外层阻塞尚需接入，不能自行重建日志恢复提交。

### HTTP 提交与原作业查询

新增 ComfyClient，将 POST /prompt 接入单次提交日志；先核对日志固定 endpoint，
提交不自动重试、不跟随重定向，响应限长且严格解析 JSON。history 仅 GET 原 UUID，
返回未经信任的历史对象；空对象不修改 sending，也不授权重发。

使用本机真实 HTTP 服务覆盖正常响应、服务收包后断连接、307、超大响应及重复
JSON key，重开 SQLite 后均不产生第二次 POST。日志与 HTTP 合跑 10 项通过，
Ruff/mypy 通过。历史内容关联、结果固定、上传下载及 DAG 仍未接入；没有运行
真实 ComfyUI，不能声明复合节点可用。

### 历史关联校验

ComfyClient.observe 只查询原 UUID，再校验历史外层键、prompt 数组内 UUID 和
实际提交图的规范化字节，拒绝另一图或另一作业的历史。成功要求 completed=true；
失败保留原 messages。未知输出节点、错误状态类型和缺失历史保持不确定，
不会重发或更改提交日志。返回值显式标记 composite_boundary_only，尚不作为
导入完成或内部来源已验证的证据。上游修改 API 图时也会拒绝，不能静默接受差异。

日志、HTTP 与历史关联测试合跑 20 项通过，Ruff/mypy 通过。下一步仍需耐久固定
观察结果、按受信输出映射下载并验证 Artifact，以及外层作业和 DAG 接入。

### 固定终态观察

匹配的历史以规范化原文及摘要持久化，与 observed phase 同事务提交。
后续 observe 从日志离线校验读取；上游历史清理不影响已固定观察。
不同历史不能覆盖原记录；observed 标记存在但内容缺失/损坏时拒绝补写，
也不重新联网。提交入口读取 observed 只返回记录，不重复 POST。

日志、历史和 HTTP 合跑 22 项通过，包含 SQL 触发器注入状态提交失败，证明
观察内容插入随事务回滚。当前只固定历史描述，输出文件尚未下载或导入，
不能把文件名或历史成功状态当作已验证资产。数据库整体丢失的外层归属阻塞
仍待集成；不声明完整 ComfyUI 恢复已完成。

### 首个输出下载边界

ComfyClient.download_image 只从已固定的成功观察中，按调用者明确指定的节点和
索引选取 images 槽。要求 output 类型、规范相对目录和单个文件名，经 query
编码后从固定服务 /view 下载；不跟随重定向、不接收任意 URL。限长并实际解码
PNG，要求显式 RGB/RGBA 模式，拒绝全透明 RGBA，不隐式转换通道。

35 项相关 CPU 测试通过，包含真实本机 HTTP 下载、特殊文件名编码、大小限制、
通道不符、非法路径和图像编码；Ruff/mypy 通过。下载字节尚未固定为 Artifact，
不能在恢复时假定同名文件内容不变；内容摘要及导入回执是下一步。真实 ComfyUI
workflow、图像上传、DAG 与画布入口仍未接通。

### 输出 Artifact 导入首片

新增 import_image：在固定观察基础上绑定 Store、输出节点/索引、编码契约、
观察摘要及部署声明摘要，下载并校验 PNG 后预先耐久写入完整 Artifact identity。
随后才写 Store。恢复时已有导入记录只能读取并验证原 Artifact：完整写入后
执行器中断可认领；写入前中断或后续 Blob 丢失均阻塞，不重新下载或补写。
同一输出更换 Store/模式也拒绝，来源关联留在导入记录而非混入 Blob 身份。

38 项 ComfyUI 相关测试、Ruff/mypy 通过，包含 Store 写入前/后故障注入。
此导入仍是内部首片：尚未将导入记录自身的身份固定到外层 BuildRun，因此
整个日志或单条导入记录丢失的检测仍需外层归属集成；不能宣称完整恢复闭环。
也尚未构建复合 provenance Artifact、上传输入或开放 DAG/画布节点。

### 受信模板的标量参数绑定

ComfyWorkflow 校验 API 图节点、连线引用与无环性，复制冻结模板，复用 AdapterSpec
参数 schema/defaults 校验实际参数。公开参数逐个映射到已有标量输入，不能替换
连线或两个参数覆盖同一槽位。绑定结果记录 template/workflow/mapping 摘要与
实际规范化参数；不同 seed 改变实际图摘要，不改变模板摘要。

首版不支持 literal array 输入或对象参数映射，避免把数组与 ComfyUI 连线混淆。
完整节点类型与输出槽位有效性仍需部署的 ComfyUI validate_prompt 核验，Core
静态检查不声称能验证自定义节点实现。47 项 ComfyUI 测试、Ruff/mypy 通过。
该组件尚未接启动 profile、输入上传、外层服务与 DAG，不是新开放的画布节点。

### 图片输入上传与回读

upload_image 从有效 RGB/RGBA PNG Artifact 读取，校验编码和摘要后以 Blob 摘要
命名，上传到受控 assets-generator 子目录，overwrite=false。回执必须保留预定
位置，随后通过固定服务 /view 回读并逐字节比较，返回 Artifact/Blob/文件位置映射。
改名、重定向、内容变化、超限或损坏输入均拒绝；上传不调用 /prompt。

52 项 ComfyUI 测试、Ruff/mypy 通过。回读只证明观察时刻的文件内容，不保证
远程文件随后不可变；服务端隔离、提交前再次核验及输入映射的耐久记录仍需
外层编排接入。当前返回的映射尚非 provenance Artifact，也没有真实 ComfyUI 验收。

### 图片边界组件串联验收

本机真实 HTTP 协议 fixture 已串联输入 Artifact → multipart 上传与回读 → 类型化
workflow 绑定 → 耐久提交 → 历史关联 → PNG 下载 → 输出 Artifact。分别覆盖正常
回执和服务收包后关闭连接，重开日志后查询原 UUID，上传与 prompt POST 均各一次。
关闭 HTTP 服务后固定观察和 Artifact 可离线恢复；删除输出 Blob 后明确阻塞，
不重下载、不补写。输入与输出采用不同图片，避免同内容复用掩盖链路遗漏。

全部 ComfyUI 测试 54 项通过；新增测试 Ruff 通过。这是组件级集成测试，fixture
没有运行 ComfyUI 节点或模型。输入映射目前由测试调用者纳入提交 deployment，
未提供生产编排入口；外层归属、防日志丢失、复合 provenance 和 DAG 仍待实现。

### 日志身份与缺失阻塞

SQLite 首次创建前写独立 journal UUID 标记，初始化由文件锁串行保护。
重新打开仅使用已有数据库，核对库内 UUID 与标记，并要求提交/观察/导入表存在；
不自动补建历史表。库丢失、标记丢失、替换为另一份有效库或删除提交表均拒绝。
标记写入后初始化中断可能留下阻塞状态，此时不得把路径当新库再次提交。

58 项 ComfyUI 测试、Ruff/mypy 通过。无标记的旧实验库不自动迁移，需保留并人工
核查，不能重建它来恢复推理。完整目录和标记同时丢失、单行删除及同 UUID 的旧库
回滚仍需要外层运行的固定身份/回执保护，本步不宣称覆盖这些情况。

### 外层固定日志与提交身份

日志提供 expected_journal_id 恢复入口，以及 submission_binding / verify_binding。
外层应在允许 POST 前耐久保存 journal UUID、submission key、prompt UUID 和请求摘要；
恢复时先核对该绑定。预期日志整个目录丢失时拒绝创建目录；替换为新日志、删除或
重新准备提交行、修改请求内容均拒绝。验证接口只读，不补写缺失提交。

62 项 ComfyUI 测试、Ruff 格式/检查及 mypy 通过。这是供外层使用的接口，尚未接入
BuildRun 或 RemoteServiceStore 的持久化路径；相同 UUID 数据库的 phase 回滚、导入
回执删除仍未由该不可变请求绑定覆盖，不宣称完整恢复或真实 ComfyUI 验收完成。

### 外层执行器保留不确定占用

服务 handler 的 ServiceExecutionUncertain 异常现在穿过执行器，不转换成 failed；
ComfySubmissionUnknown 继承此类型。上游可能已收包时，外层 job 保持 running，
串行队列继续阻塞。已确认的普通 handler 错误仍保留原有失败处理。

86 项 ComfyUI 与服务相关测试通过，包含一次收包后断连接、重开外层数据库和内部
日志、再次执行及排队任务均被拒绝，prompt 回调总计仅一次。Ruff/mypy 通过。
这只补齐执行器异常语义；查询恢复和复合 handler 的生产集成仍待完成，不能通过
本地进程退出或任意异常将远程未知状态宣布为终态。

### 外层作业绑定与查询恢复入口

RemoteServiceStore 的 job 行新增可空 comfy_binding，兼容已有作业。领取为 running
之后，submit_owned_prompt 先准备内部日志，再以事务固定外层绑定，只有首次成功
写入者获得发送资格。并发连接及重复调用不得再次发送；恢复通过独立的
recover_owned_prompt 入口核对 journal/提交身份，再查询或读取固定观察。

内部目录丢失、提交行删除、prepared 状态回滚均保持不确定并阻塞。授权已写入但
尚未发送时崩溃，也保守阻塞，不自动恢复发送。此实现依赖外层服务数据库完整；
不承诺同时回滚或丢失外层与内部全部证据后仍能识别历史执行。

93 项 ComfyUI 与服务相关测试、Ruff 格式/检查和 mypy 通过。新增覆盖正常历史
查询恢复、响应丢失、缺失日志、状态回滚、并发只授权一次及旧 job schema 升级。
测试使用注入传输，不是真实 ComfyUI。入口返回提交回执或终态观察，尚不负责输入
上传、输出导入、复合 provenance 或发布；尚未成为可配置的 service handler/DAG 节点。

### 图片端口与普通参数分离

ComfyWorkflow 新增独立 image_targets。普通参数 schema 不再需要暴露图片路径；
图片绑定要求完整上传回执，核对固定服务、内容命名、位置、验证方式及 Artifact/
Blob 摘要格式。图片槽位不能覆盖普通参数或内部连线，缺少或多出图片均拒绝。
绑定结果保留输入回执副本，mapping digest 升至 comfy-input-map@2 并覆盖图片映射。

77 项 ComfyUI 测试、Ruff/mypy 通过。HTTP 串联 fixture 已使用 upload_image 产生
的真实回执绑定图片，同时保留普通 seed 参数。回执字段校验不等于认证：生产调用者
必须由受信上传路径获得回执，不能将用户提交的 JSON 当作已验证上传证据。本步仍
未开放 DAG/画布节点，也没有完成生产复合 handler 或真实 ComfyUI 验收。

### 复合边界证据值

image_boundary_evidence 从已固定的成功观察、完整 workflow 绑定和已有图片导入回执
组装 ComfyImageBoundary@1.0，沿用 remote_job_result kind。记录输入输出 ArtifactRef、
提交身份、实际 workflow/参数/映射、输出槽位及观察/导入摘要；部署声明与内部验证
状态分开，Comfy revision、自定义节点和模型身份均明确 unverified。

该函数只读取，不联网、不导入、不修复。输入输出缺失或摘要不符时拒绝。77 项
ComfyUI 测试及 Ruff/mypy 通过，HTTP 链覆盖结构化 Artifact 持久化往返、标准引用
遍历发现输入输出、离线重建证据值和删除输入/输出后阻塞。此处只组装证据值，
调用者仍需在外层完成结果引用的预分配和耐久固定；不能反复持久化来补回丢失证据。
尚未接生产复合 handler、DAG 或画布，也没有真实 ComfyUI 验收。

### 外层结果预分配与离线采用

外层 job 行新增 comfy_result，fix_image_result 先以事务固定 Store、证据 Artifact
身份及提交绑定，再持久化 ComfyImageBoundary。已有预分配只能通过
recover_image_result 读取原 Artifact 和依赖闭包；不再组装并补写。原 journal 或
远程服务不参与本地结果读取。输出映射更改拒绝复用；此接口不改变 job 终态。

ComfyUI 与服务相关测试 105 项通过，覆盖正常/丢回执 HTTP 路径上的证据写入前后
中断：写前阻塞、写后采用、随后删除证据仍阻塞。补充输出映射检查后重跑 HTTP
链 6 项通过；Ruff 格式/检查和 mypy 通过。测试调用者仍负责先完成图片导入；
输出导入回执尚未与外层预分配形成同一个完整生产入口，不能宣称整个复合 handler
恢复已闭环。真实 ComfyUI、DAG/画布接入仍待完成。

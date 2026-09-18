# 固定流程节点工作台 v1

状态：设计提案，尚未实现。日期：2026-09-18。

本文定义下一条实现切片，不改变现有 CLI 的行为，也不表示现有 workflow 已支持断点恢复。
目标是让用户在一个入口完成照片到单对象资产发布，同时建立可以验证的执行恢复语义。
不先迁移全部 workflow，也不先实现通用节点编辑器。

## 1. 首版范围

固定模板 `photo_object_asset@1`：

```text
导入照片 → SAM mask 候选 → 人工选择/取反/清理 → 单图生成与标准发布
                                              └→ AssetDefinition / AssetRelease / QA
```

工作台显示四个步骤、参数、预览、实际状态和发布结果。最后一个步骤是现有单图工作流的
子运行，包含 RGBA 准备、生成、canonicalization、组装、QA 和导出；其内部节点可查看，
首版不展开成可编辑画布。SAM 和 Shape Backend 继续运行于独立环境，GPU 任务串行。

首版支持：

- 导入照片、选择已配置 Backend、设置允许的推理参数。
- 候选叠加预览、选择一个 proposal，可取反并保留最大连通区域，确认最终 mask、记录自报检查人。
- 等待人工操作时关闭服务，重启后恢复相同任务。
- 失败可定位到 stage/子运行；显式重试中断或失败的计算。
- 查看发布资产、QA 和证据；从原配置创建新运行。

不包含自由连线、节点增删、通用循环/分支、批量多对象、人工布局、跨机器调度、GPU 内部
断点恢复、取消正在运行的推理、自动 fallback 或通用跨 Run 计算缓存。现有 CLI、独立审查
页面和模型接口保持可用。节点工作台是固定流程的产品入口，自由编排是否值得实现由此切片验证。

## 2. 当前代码事实与复用边界

- `BuildRun` 支持 running 和阶段性 NodeAttempt，但各 workflow 落盘时机不同。当前
  `build_image_asset()` 的运行和节点更新主要在内存，持久化集中于发布或失败；
  `Phase1Runtime.run_node()` 尚无逐节点保存。执行前登记、节点开始/结束落盘是新增工作。
- `LocalArtifactStore.record_build_run()` 每次生成不可变快照，再原子更新 `runs/<run_id>.json`
  指向最新快照。索引可变、快照不可变；原子替换本身不提供并发互斥。
- `ResolvedPlan` 已绑定单图 Pipeline、OperatorSpec 和 Backend；其中 implementation 是进程内
  对象，不能直接当作可重启的 JSON 计划。重启时需按持久化描述重建并检查绑定。
- `propose_instances()`、`select_instance_proposals()`、`build_image_asset()` 可作为步骤适配器。
  现有选择允许多个 proposal，本模板额外要求恰好一个，底层选择契约不因此收窄。
- 现有 SAM proposal 含逐 mask 的 provenance 引用。相同 mask 重跑后，完整 proposals Artifact
  不保证同 ID；不能直接把其 artifact_id 当作稳定候选内容摘要。
- 现有 review 记录与输出仍是权威证据。前端不得自行构造一个布尔值替代决定 Artifact。

步骤适配器只负责参数转换、输入绑定和调用既有边界，不复制生成、坐标变换或发布算法。
路径型 API 所需的中间文件由服务端在运行私有目录物化，用户和浏览器只使用受控 ID。
必要的 run_id 注入、输出引用返回等兼容扩展限于这三个入口，不重构其余 workflow。

当前 `build_image_asset()` 已接受 `run_id: str | None`，并拒绝已存在的运行索引；保留该行为，
补充工作台子运行登记上下文。`propose_instances()` 和 `select_instance_proposals()` 尚需
增加可选 run_id/父运行上下文，旧 CLI 不传时仍自行创建运行。当前 LocalProcessWorker 的
job/Popen 索引仅在内存中，不能用于服务重启后的进程判定；新增机制见第 7.1 节。

### 2.1 mask 处理及生成输入的复用边界

当前选择入口已有 invert / keep_largest，可产生处理后 mask、派生 proposals 和 provenance。
工作台复用其算法，补齐规则版本、预览与确认绑定，不重新实现浏览器清理算法。

复用或抽取 `extract_scene_objects()` 的选择与实际 mask 一致性校验。生成适配器必须解析
InstanceSelection 实际引用的 proposals（处理后可能是派生 proposals），核对唯一选中项、
原图及最终 mask，并验证处理链回到原候选。不能拿原 proposal 的 mask 绕过人工处理。

调用单图子流程前保存输入绑定证据：selection、原/派生 proposals、原图、原 mask、最终 mask、
处理参数及规则版本。生成 stage 的 resolved_inputs 包含该证据。服务端从引用物化只读私有
输入，不接受浏览器另传 mask 路径。单图入口重新导入后，在首次推理前核对实际输入；若
Artifact ID 因导入 schema/metadata 不同而变化，必须验证 Blob 内容、尺寸、通道及二值语义，
记录旧/新引用和验证方法，不能仅比较路径。

子 BuildRun 输入及生成 provenance 通过绑定证据回查人工决定和最终 mask。决定证明输入
选择来源，不把 generated 几何改标为 user。仅有父子运行关联不足以证明实际输入正确。

## 3. 计划与执行事实分离

### 3.1 不可变 WorkbenchPlan@1.0

新增 `workbench_plan` Artifact，保存以下可序列化描述：

| 字段 | 含义 |
| --- | --- |
| template / template_version | 固定模板名称和版本 |
| input_refs | 已导入原图的 ArtifactRef |
| stages | 稳定 stage_id、adapter 名称/版本、具名输入绑定、参数 |
| contract_digests | 所用模板、adapter 契约和 OperatorSpec 的摘要 |
| backend_bindings | Backend 名称、版本、模型/runner/环境身份、允许的配置 profile |
| child_plan | 单图 ResolvedPlan 的持久化描述及 contract digest |

输入绑定只允许引用已导入输入，或 `stage_id + output_port`；编译时检查依赖无环、端口
kind/cardinality/schema，以及模板所要求的人工选择数量。现有 OperatorSpec 是算子端口的
唯一来源，子工作流适配器只声明聚合输入/输出，禁止再复制一套内部模型端口。

首版不开放用户编辑 stage 列表。步骤顺序和绑定由计划交给执行循环解析，不能再在 HTTP
handler 中写第二套业务调用顺序。UI 坐标、折叠、日志路径不参与执行计划摘要。

Backend profile 的路径是运行位置，不是模型身份。启动前解析现有本地身份信息，不下载
模型；不能核实绑定时阻止执行。恢复时不得只凭名称悄悄切换模型。执行完成还须校验 Backend
报告的实际身份与计划匹配。配置或身份变化要求新计划、新运行。

### 3.2 扩展现有 BuildRun

不新增平行的任务事实库。为工作台运行增加可选字段，缺省值使旧记录仍可读取：

```text
plan_ref                 不可变 WorkbenchPlan 引用
state_revision           单调递增，用于拒绝过期操作
stage_states             stage_id → 当前状态、当前 attempt、成功输出/待办引用
parent_run_id            可选，子运行关联
```

工作台 stage 的 attempt 另记录：`resolved_inputs`、`input_digest`、`child_run_id`、
`outputs`、`error_code`、时间及执行方式。实现时可用有类型的扩展字段；不得把恢复所需的数据
藏进 UI 日志。成功输出是精确 ArtifactRef，人工输出还包含 decision/provenance 引用。

attempt 的恢复扩展还包括 `command_receipt`、`child_registration` 和 `worker_execution`。
后两者分别保存子运行所有权和进程执行证据，不另建任务数据库；定义见第 4.1、7.1 节。

计划保存“将执行什么”；BuildRun 保存“实际输入什么、输出什么”。运行后的 ArtifactRef
不得写回计划。历史 attempt 不覆盖；增加 attempt 并保留前次失败/中断原因。

### 3.3 恢复状态的字段归属

以下是运行扩展的最小字段契约；实现须使用有类型结构和版本化序列化，不能分散成任意 dict。
引用字段统一使用 ArtifactRef；缺省的可选字段为 null，集合为空集合。时间、UUID 和进程探测
结果由外部事件提供，不在状态迁移函数中生成。

| 结构 | 必须保存的字段 |
| --- | --- |
| Run 扩展 | schema_version、plan_ref、state_revision、stage_states、parent_run_id（可选） |
| StageState | stage_id、status、active_attempt、attempts、request_ref（可选）、draft（可选） |
| StageAttempt 扩展 | attempt、resolved_inputs、input_digest、child_run_id（可选）、outputs、execution_mode、started_at、finished_at、error_code、retry_blocked_reason |
| CommandReceipt | idempotency_key、request_digest、command_kind、status（prepared/completed/failed）、child_run_id（可选）、output_location（可选）、result_refs |
| ChildRegistration | child_run_id、parent_run_id、stage_id、attempt、input_digest、registration_location |
| WorkerExecution | job_id、child_run_id、launch_request_digest、launch_phase、host_id、boot_id、pid、starttime_ticks、pgid、exit_code、last_probe |

StageAttempt 持有 command_receipt、child_registration 和 worker_execution；计算步骤无需
人工回执时保留空值。outputs 仍使用 NodeAttempt 既有端口值结构，不能再建另一份输出表。
stage 的 succeeded 输出从其成功 attempt 读取；StageState 不另存可能失同步的输出副本。
last_probe 包含探测时间、observed_identity 和 alive/exited/unknown 结果，不视为永久有效证明。
launch_phase 为 prepared、identity_recorded、release_authorized、exit_observed；其中
release_authorized 表示已允许放行，不声称消息已送达或模型已开始运行。

工作台创建运行命令发生在 run_id 产生之前，其幂等回执保存在工作台受控目录的请求索引中，
绑定请求摘要与预分配 run_id。先持久化索引再创建父运行；重发查询该 ID，不能再创建第二个。
此索引是定位/去重元数据，业务状态始终以 BuildRun 为准。

## 4. 最小执行器和状态机

stage 状态：`pending / running / waiting_for_input / succeeded / failed / interrupted`。
父运行使用同名状态子集；所有步骤成功后才是 succeeded。人工等待时 finished_at 为空，
不以 failed、skipped 或 succeeded 代替 waiting_for_input。

```text
计算步骤：pending → running → succeeded | failed | interrupted
人工步骤：pending → waiting_for_input → succeeded
重试：failed / interrupted → 新 attempt 的 running
```

`interrupted` 仅由恢复核对发现执行失联或进程异常消失时产生，不是取消命令，也不证明
旧 Backend 已退出。仍活跃或状态不明的进程必须阻止重试。已收到明确失败结果的调用记为
failed；既有超时终止策略保持不变。首版不提供用户主动取消推理的 API。

执行循环从计划查依赖，解析成功输出，验证契约和 digest，再分派已注册的步骤适配器。
仅有一个串行执行者。HTTP 仅提交命令、返回运行 ID，浏览器轮询状态；长推理不占用请求处理。

首版一个本地工作台服务独占其工作台运行目录，采用操作系统文件锁持有服务生命周期。
每个命令在锁内验证 expected_revision，再写下一快照；所有后台更新也经过同一写入入口。
禁止多个服务写同一工作台运行。现有 CLI 若独立使用 Store，不能接管这些父/子 run_id。
不实现数据库、租约集群或远程任务队列。

所有工作台状态更新统一经过纯函数：

```text
transition(current_state, event) -> next_state + requested_effects
```

event 携带 event_id、expected_revision、目标 stage/attempt 及有类型载荷。函数只校验状态、
计算下一 revision 和声明需要的副作用，不访问文件系统、不探测进程、不调用 Backend。
外层驱动按“迁移 → 耐久提交 → 执行副作用 → 将结果作为新事件”运行。只有提交成功才能
响应命令或放行模型；磁盘提交失败丢弃内存中的 next_state，不执行后续副作用。

最小事件集合为 PrepareStage、ChildRegistered、LauncherIdentified、AuthorizeLaunch、
HumanRequestReady、MaskPreviewReady、DecisionPrepared、ChildSucceeded、ExecutionFailed、RecoveryObserved、
RetryRequested。它们分别约束输入已解析、子登记可查询、身份已确认、放行获准、待办已生成、
提交回执已建立、成功证据已核实、失败事实、恢复探测结果及显式重试。MaskPreviewReady
仅更新待办草稿及预览引用，不完成 stage 或产生人工决定。人工准备中断由
RecoveryObserved 按第 7 节重建待办；恢复不能虚构 ChildSucceeded。

迁移必须保持：attempt 单调增加、成功输出不可覆盖、待办绑定精确输入、每次最多一个活动
计算、旧 revision 不得更新状态、无有效登记和进程身份不得授权放行。重复命令优先按回执
验证请求摘要并返回原结果，不因其 expected_revision 已过期而创建新副作用；不同内容仍拒绝。
完成的命令回执保留在历史 attempt 中。首次创建运行索引采用独立的创建入口和同样的耐久顺序。

副作用可能已完成但结果事件未提交，恢复必须按回执核对实际结果，不能单靠再次调用
transition 保证 exactly-once。这里不引入事件日志数据库或通用事件总线；事件是可测试的
函数输入，BuildRun 快照仍是状态的持久化载体。

### 4.1 子运行登记顺序

新增最小的子运行登记入口，供三个适配器共同使用：

1. 父 attempt 先保存回执，含预分配 child_run_id、parent_run_id、stage_id、attempt、输入
   摘要及输出目录；尚不能启动 Backend。
2. 子入口在开始执行或持久化业务输出之前，独占创建该 run_id 的所有权登记，检查父回执
   一致，再调用 record_build_run 建立 running 快照与 runs 索引。登记至少绑定父 attempt，
   对同一 ID 的其他调用拒绝；旧 CLI 创建运行也必须检查该 ID 未被预留。
3. 子入口记录 parent_run_id 和关联 stage/attempt。登记并建立索引成功后才允许业务执行。
   重复调用不能清空已有子运行；适配器返回已有状态，由恢复逻辑决定采用结果或重试。

所有权登记使用受控运行目录内的原子排他创建（如 O_EXCL），内容须与父回执匹配；它是
运行索引的所有权元数据，不是第二份业务状态。仅有登记、没有有效索引时不能推断执行成功。
重启时若索引缺失或损坏，标记 interrupted 并报告原因，不扫描全 Store 猜测“最新子运行”。
由于执行必须晚于索引建立，正常登记中断不会启动推理；若可能是索引损坏，仍检查进程证据。
重试使用新的 child_run_id，保留旧登记用于审计，不冒用旧 ID 重新开始。

用户改图片、SAM/Shape 参数或 Backend 时，新建计划及父运行；不原地修改已运行计划。
首版保留旧运行，不自动重算历史。开始运行前可编辑未持久化的草稿。

## 5. 人工待办和决定

进入人工步骤前持久化不可变 `HumanInputRequest@1.0`（kind `human_input_request`）：

```text
run_id / stage_id / attempt
input_refs                  精确 proposals 和 image 引用
review_evidence_digest      第 6 节定义的稳定证据摘要
decision_contract           single-proposal-mask-edit@1
allowed_actions             select_one, invert, keep_largest
```

待办引用写入 BuildRun 后，接口才返回 waiting。刷新或重启从 Store 重新读取，不依赖浏览器
内存或 localStorage。空候选运行可成功，但人工待办显示“无可选对象”，不能提交空选择；
用户调整分割配置创建新运行。

提交必须带 request_id、expected_revision、幂等键、proposal_id、reviewer、invert、keep_largest、
规则版本及已预览的最终 mask 引用。服务校验待办
仍有效、选项属于该候选、当前输入身份未变，调用既有选择边界生成 InstanceSelection、
user-source provenance 和子 BuildRun，再绑定至父 stage。决定的 source 不由前端自由指定。

处理契约固定为 `mask-edit-v1`：先可选取反（0/255 互换），再可选最大连通区域保留；
采用 8 邻域，等面积取行优先首个区域。不启用的步骤为恒等操作，禁止交换处理顺序。
处理后必须与原图同尺寸、非空且二值；重算 bbox/area，不能继承原 SAM 分数作为处理结果
分数。原 mask 不变，处理结果和 user-source 派生证据持久化；不处理时使用原 mask 引用。

预览由服务端调用与提交相同的确定性函数，返回最终 mask 引用及完整规则描述；预览不产生
人工确认。待办的当前草稿（proposal、参数、规则、预览引用）经 revision 校验保存到
StageState 的可选 draft 字段，重启恢复草稿叠加图，不改写不可变待办。修改参数使旧预览
失效；提交时重算/验证与引用一致，成功后显示决定绑定的最终 mask。不能将勾选状态当作
确认结果。规则、顺序及全部参数均进入决定证据、幂等请求摘要和人工步骤 input_digest。

同一命令重发返回原结果；相同幂等键但不同请求体返回冲突。已经完成的待办不能被另一次
提交覆盖。想改变选择须创建新运行，旧决定不可删除或改写。检查人仅是自报身份，不是认证。

幂等命令采用持久化回执：在调用子流程前记录规范请求摘要、预分配 child_run_id 和输出位置。
回执保存在该 stage attempt 的运行快照中，完成后记录返回引用。断开 HTTP 连接不撤销任务。
回执本身不参与 input_digest：其中 child_run_id、输出位置、时间和进程信息属于执行事实。
业务请求字段经规范化后按第 6.1 节参与摘要；幂等请求摘要和计算输入摘要分别计算。

## 6. 输入摘要与失效规则

### 6.1 计算步骤

```text
input_digest = hash(canonical_json(
  adapter + version + contract_digest + named_actual_inputs + parameters + backend_identity
))
```

实际输入包括 Artifact ID；结构化值使用规范序列化的内容；数组顺序有意义。路径、UI 布局、
时间和运行 ID 不进入计算输入摘要。恢复时检查摘要与输出完整性，缺失/损坏不得继续使用。
摘要相等不保证模型重跑得到相同字节，也不自动授权 best_effort Backend 跨运行缓存。

首版恢复的是本运行已完成输出，不承诺重算可复现；标记 restored 与 executed，不能把恢复
描述成“模型重放”。既有子流程缓存策略保持原有边界，子 BuildRun 记录真实 cache_hit。

### 6.2 人工证据

定义版本化投影 `proposal-review-evidence-v1`，摘要包含：

- 图像 Artifact ID；候选的显示顺序。
- 每个候选的 proposal_id、mask Artifact ID、bbox、area、label、source 和展示分数。
- 模型 checkpoint、runner/environment 身份及 AMG 参数；决策契约版本。

从投影中排除 mask provenance 引用、run_id、时间及物化路径。这些记录继续通过完整 proposals
引用保留，不从原 Artifact 中删除。投影是显式字段白名单；不能笼统排除 metadata。所有 UI
中会影响决定的候选事实必须来自该投影；字段/语义变化应升级投影版本。

规则：

| 情况 | 处理 |
| --- | --- |
| 相同精确输入待办恢复 | 保留原待办及决定，先校验完整性 |
| 参数仅改 Shape Backend，候选证据未变 | 新运行可提议复用 mask 决定，需用户显式确认 |
| mask、顺序、分数、模型身份或决策契约变化 | 不允许复用旧 mask 决定 |
| invert、keep_largest、处理顺序或规则版本变化 | 不复用旧处理决定，须重新预览确认 |
| 字节相同但空间/通道等 Artifact 语义不同 | Artifact ID 不同，视为证据变化 |
| 重跑只改变 provenance，投影相同 | 可提出复用已有决定，不自动套用 |

跨运行复用决定必须由用户显式确认，绑定旧决定、新 proposals、相等的两个证据摘要和当前
操作者。产生新的 InstanceSelection 与 provenance，保留 `reused_from` 的证据关联；不得让
现有 extraction 验证器接受指向旧 proposals 的选择。现有选择格式的扩展应保持旧记录可读。
首版只支持用户指定旧决定引用，不做全 Store 自动匹配推荐。计算缓存不由此规则放宽。
同一运行的恢复保留已绑定的决定；新运行即使证据相同，也不能自动继承决定。这两档规则
与配置是否影响 mask 的语义分开：证据相同只表示允许复用，不表示已获确认。

候选证据摘要描述原候选集；处理决定还须匹配原 proposal、全部处理参数及最终 mask 身份。
跨运行复用时验证处理结果一致，并让新选择指向新运行实际的原/派生 proposals。

## 7. 恢复、故障窗口与发布

重启后逐项执行：

1. 获取工作台锁，读取父运行最新快照，验证计划引用和契约版本。
2. 验证已完成 stage 的 input_digest、输出及其必需依赖闭包。包括 proposals 的全部 mask、
   selection 的候选引用，以及 release 的 asset/files 和恢复所需的 provenance。
3. waiting_for_input 恢复原待办。失败或中断等待显式重试，不自动重复模型调用。
4. 父 stage 为 running 时，按预记录 child_run_id 查询子运行并核对计划、输入、输出。
   子运行成功且产物完整则补记父 stage 成功；证据不足则标记 interrupted。
5. 推理尚在执行时先确认旧 Backend 进程已退出。不能确认则阻止重试并要求用户处理，不能
   仅凭服务锁释放就认为 GPU 子进程也已退出。首版不实现进程内模型恢复。
6. 人工 stage 若没有命令回执也没有有效待办引用，视为待办准备被中断。核对前序输入后可
   幂等重建待办并恢复 waiting_for_input，不重跑模型、不生成选择决定。若已有提交回执，
   先按 child_run_id 核对决定执行，不能退回一个允许重复提交的新待办。

### 7.1 Worker 执行证据与孤儿进程

首版 launcher 仅托管 Backend 命令及其进程组，不承载完整计算 stage。导入结果、
canonicalization、QA、组装和发布仍在工作台服务的 Core 中执行。服务崩溃后，孤儿 Backend
即使计算完成，也不保证有人继续 Core 后处理；首版不接管未完成的后处理，不根据临时模型
输出跳过推理。只有成功子运行和完整发布证据均存在才恢复成功，否则核实旧进程退出后
显式新 attempt 重试，可能重新运行模型。完整 stage 独立执行进程不属于本切片。

首版采用 Linux 本地进程语义。运行前持久化 `worker_execution`：job_id、child_run_id、
stage/attempt、启动请求摘要和 launch 状态；启动握手再记录 host identity、boot_id、PID、
`/proc/<pid>/stat` 的 starttime ticks、进程组 ID。墙钟 started_at 仅供展示，不能代替 starttime
判断 PID 是否被复用。终止后记录 exit 状态；这些证据不参与模型或 Artifact 内容身份。

不能采用“Popen 启动模型后才补写 PID”的无保护顺序。工作台模式下 Worker 使用启动门控：
先启动轻量 launcher，launcher 报告进程身份后等待放行；父运行持久化握手信息并确认成功后
才放行执行 Backend。launcher 在放行前发现控制通道断开则退出，不导入模型。放行后即使
父服务消失，也已有可供恢复查询的进程身份。原有 CLI Worker 行为无需全量迁移。

这里的“持久化成功”必须是耐久提交，不只是 write/flush/os.replace 返回。放行前按依赖顺序
确保回执、子登记、输入/计划引用及身份快照所依赖的 Blob/manifest 已落盘：写文件并 fsync，
原子发布后 fsync 所在目录；最后写 runs 索引临时文件并 fsync，替换索引后 fsync 索引目录。
新建目录也同步其父目录。状态为 release_authorized 的快照及索引完成上述步骤后，才发送
一次放行消息。任何写入/fsync/替换失败均不放行，关闭控制通道让等待中的 launcher 退出。
现有 record_build_run 的原子替换不自动满足此契约，需要工作台路径的耐久提交实现。

不要求“已发送”再做一次持久化确认：release_authorized 必须保守理解为可能已执行。
此状态恢复时通过登记的 launcher/进程组身份以及子运行结果核对，不重发放行消息；新服务
不会重新连接并激活旧 launcher。若没有有效登记，不扫描任意进程猜测任务归属。
断电重启本机会结束旧进程，但仍需保证输出/索引一致；存储承诺以支持文件及目录 fsync 的
本地文件系统为边界，不声称覆盖硬件违约或远程文件系统的耐久性。

工作台 Backend 使用独立进程组，launcher 在其直接子任务结束前不退出；不支持脱离该组的
守护进程式 Backend。恢复检查已登记进程及组内任务，不能只看 launcher 是否仍存在：

- 同一 host/boot，PID 与 starttime 匹配且仍运行：显示旧任务仍活动，禁止再次提交推理。
- 已退出且相关进程组已清空：按子运行/发布证据决定成功恢复或 interrupted 后显式重试。
- PID 已复用：不得向该 PID 发信号，也不能据此断言旧进程组已清空。
- 同主机已重启：旧 boot 的进程不可能仍存活，仍须核对持久化输出，不能推断执行成功。
- 主机不同、权限不足、身份或进程组无法核实：记录 retry_blocked 原因并阻止重试，不猜测退出。

异常遗留进程由用户在服务外处理；服务重新核实后才解除阻塞，不提供“忽略证据强制继续”。
launcher、启动门控和上述持久化字段是本切片的前置实现，不以当前内存 Worker 冒充完成。

进程探测通过可替换的 ProcessProbe 接口返回进程身份、组成员和 alive/exited/unknown；
持久化提交与启动通道也提供故障注入点。默认实现读取本机 Linux /proc，测试可注入 PID
复用、权限不足、跨 boot 等证据，不要求为了单元测试真的制造 PID 复用。

关键故障窗口：

| 故障时刻 | 恢复行为 |
| --- | --- |
| 待办已写入、响应未送达 | 返回同一待办 |
| 人工待办尚未建立，无提交回执 | 校验前序输出，幂等建立待办，恢复 waiting |
| 子运行登记/索引写入中断 | 不启动业务；缺失索引明确报告，保留登记并用新 ID 重试 |
| launcher 已启动，身份尚未持久化 | 未放行模型；控制通道断开后 launcher 退出 |
| 身份或授权快照的 fsync 失败 | 禁止发放行，关闭控制通道；恢复不得依据未提交内存状态 |
| 授权已耐久提交、发送前或发送后崩溃 | 不区分消息是否送达；核实 launcher/进程组与子结果，绝不自动重发 |
| 已放行 Backend，父服务崩溃 | 由持久化进程身份核实活动状态，活动/未知均阻止重试 |
| Backend 已结束、Core 后处理/发布未完成 | 模型退出码不代表子运行成功；旧进程退出后显式重试，可能重跑模型 |
| 人工子运行成功、父记录未更新 | 从命令回执及子运行补齐，不能重复创建决定 |
| 生成子运行成功、父记录未更新 | 校验并采用原输出，不重复推理 |
| 子运行仅有 running 记录 | interrupted；显式重试使用新 attempt/child_run_id |
| 发布目录已出现、响应丢失 | 校验目录内 release/run 身份，返回原发布结果 |
| Store 中有 Artifact、缺少成功执行/发布证据 | 不推断成功，不按目录时间或最新文件猜测 |

父 stage 的 succeeded 以完整结果和成功子运行为依据。最终目录必须原子、不可覆盖发布；
若现有子流程在重命名前写 succeeded，恢复还必须校验其发布目录，不能只信状态字段。
运行目录/发布目标在调用前写入回执，不参与内容身份。首次创建 task 时也使用幂等键，避免
重复点击创建两个 GPU 任务。输出损坏报告明确错误，首版不做自动修复或垃圾回收。

## 8. 本地 API 与工作台

建议接口（名字可在实现时调整，语义必须保留）：

| 接口 | 行为 |
| --- | --- |
| POST /inputs | 上传并校验 RGB，返回受控 Artifact 引用 |
| GET /templates、GET /backends | 固定模板及已配置实现/参数范围 |
| POST /runs | 以草稿生成不可变计划、创建运行，返回 run_id |
| GET /runs/{id} | stage 状态、待办、子运行及输出引用 |
| POST /runs/{id}/decision | 幂等提交人工选择或显式复用 |
| POST /runs/{id}/mask-preview | revision 校验后保存编辑草稿，返回服务端处理 mask；不确认决定 |
| POST /runs/{id}/retry | 基于 revision 重试失败/中断 stage |
| GET /runs/{id}/outputs/{key} | 受控图片、mask、GLB 和发布包下载 |

不提供客户端任意路径读取、任意 Python 命令、任意 Store URI 或模型安装接口。绑定
127.0.0.1，写操作校验 Host/Origin/session token；上传限制字节数与解码尺寸。后台重启后
重新获得会话 token，token 不作为决定身份。Backend 权重路径和解释器由本地管理配置提供，
页面仅显示可用实现和允许参数；环境不满足时在运行前报告原因。

工作台中的几个维度独立显示：

- 执行：待运行、运行中、等待人工、失败、中断、完成、恢复了既有输出。
- QA：读取具体 QualityReport 的 pass/warn/fail/skipped，展示检查对象和 profile。
- 人工决定：显示选项、检查人和绑定的候选，不称为“质量通过”。
- 资产边界：汇总 scale/source、已有检查和缺少的检查；无记录显示“未评估”，不编造 skipped。

执行完成不能涂成“所有质量合格”。首版不新增通用质量评分或能力认证系统。浏览器仅保存
临时编辑状态，运行、待办、决定和产物都能由后端恢复。模型依赖 CDN 时须明确加载错误；
页面失败不得修改后端运行状态。

## 9. 实现拆分与退出条件

按顺序交付，前一项提供后一项需要的可验证契约：

1. **schema 与持久化状态**：落实第 3.3 节字段、纯 transition、输入/证据摘要、回执、
   子登记及耐久提交顺序；定义可替换 ProcessProbe、启动通道与故障注入接口。退出条件为
   往返兼容、迁移不变量、过期/重复事件和各提交失败点测试通过，不依赖 GPU 或真实 PID。
2. **三个适配器与固定执行循环**：为 SAM/选择增加可选 run_id，复用单图已有参数；接入父子
   登记、恢复/重试和查询，增加单图启动及节点边界落盘；接入处理预览与推理前输入绑定校验。
   以 Fake Backend/假进程探测跑通完整模板及人工等待，故障注入
   覆盖子成功父未更新等窗口。此时不能宣称真实进程恢复已支持。
3. **launcher 与真实进程身份（主要实现风险）**：实现门控、进程组约束、/proc 探测和孤儿
   核对。在可控 Linux 环境用无 GPU 的测试子进程验证父进程消失、控制通道断开及阻止重跑。
   受限容器无法执行的集成检查单独标记未验证，在目标环境补齐后才准入真实 Backend。
4. **固定工作台**：统一预览、参数、人工选择、状态和下载，复用后端证据边界。
5. **真实验收**：已有独立 SAM/Shape 环境串行运行一例，持久化报告后决定是否扩展多对象、
   场景布局或自由编排。不据此宣布所有 workflow 统一完成。

页面不必等第 3 步全部验收后才开始：第 2 步即可用最小页面和 Fake Backend 贯通
“上传 → 选择/取反/清理 → 生成 → 查看”；真实模型仍须等待进程托管验证通过。
分别报告可用性和恢复验收，页面可用不表示恢复完成，后台恢复也不能替代实际页面验收。

共享代码只抽取本切片直接需要的调用/发布/恢复辅助能力，不顺带引入通用 Scheduler、
Plugin System、事件总线、远程 Worker、Schema Registry 或全仓库 release 大算子。

## 10. 验收矩阵

| 测试 | 必须证明 |
| --- | --- |
| schema 往返与旧记录读取 | 计划/待办/运行扩展可往返；旧 BuildRun 无新字段仍可读 |
| 纯迁移事件序列 | 重复/过期/乱序事件不破坏不变量，副作用不能先于持久化提交 |
| 提交及 fsync 失败注入 | 文件、目录和索引同步失败均不放行模型，不能发布内存成功状态 |
| 等待时强制结束服务并重启 | 同一待办恢复，前序输出 digest 不变，SAM 调用次数不增加 |
| 人工待办创建前崩溃 | 重启可建立待办，不停留在无操作入口的 running |
| 子登记、索引或启动握手中途崩溃 | 未登记完成不执行模型，重启不重复启动已有 Backend |
| 父服务消失但 Backend 仍运行 | 持久化身份可识别；重试被阻止，PID 复用不会误杀无关进程 |
| 生成中断并重启 | 不伪造成功；确认无旧子进程后显式重试，旧 attempt 保留 |
| 子流程成功、父更新前故障 | 使用预分配 child_run_id 恢复，不再调用模型 |
| 双击、请求超时重发、两客户端提交 | 只有一个有效决定/活动计算；旧 revision 返回冲突 |
| 输入/输出损坏 | 缺失或 digest 不符明确失败，不能继续下游 |
| mask 取反/清理及恢复 | 顺序、8 邻域、tie-break 正确；恢复显示实际草稿或确认 mask |
| 选择与生成输入不一致 | 推理前拒绝替换 mask；正常输入保留导入映射和决定证据 |
| 处理决定复用 | 参数/规则/最终结果变化拒绝复用，相同条件仍需跨运行确认 |
| 候选重新生成 | 仅 provenance 变化可显式复用；mask/分数/模型变化拒绝复用 |
| 修改 Shape 参数 | 新计划/运行；候选相同时可显式复用选择，不复用旧生成结果 |
| 发布前后故障 | 无半成品最终目录；已有结果可返回；不覆盖他人目录 |
| UI 刷新与错误恢复 | 人工待办/结果不丢失；未知 QA 不显示 pass |
| 真实 SAM → 选择 → Shape → Release | 引用、父子运行、user/generated 来源与实际产物一致 |

产品验收使用用户提供的机器人图片（在运行报告记录实际输入身份，不将图片提交 Git）：
在同一页面完成上传、取反、最大连通区域清理、选择 TRELLIS.2、生成和查看，不复制路径或
Artifact ID。报告分别记录操作链路是否通过及生成质量观察，不预设模型质量合格。

恢复验收：在第三步人工选择时杀掉服务，重启后校验并恢复原证据，完成选择与标准发布，
证明未重复执行已经完成的 SAM；再用故障注入覆盖提交/发布边界。首版只有一个人工节点，
不为验收人为增加三个审查步骤。

测试使用小型合法 fixture；真实模型 smoke 不替代质量 benchmark。构建、Ruff、mypy 和
既有测试全部保持通过。设计文档本身不构成任何上述功能已实现的证据。

验证分三层：纯迁移与假探测覆盖状态分支；普通 Linux 测试子进程覆盖门控、控制通道和进程组；
目标环境执行真实 Backend smoke。纯函数测试不能证明 fsync、信号或进程退出真的生效，
进程 kill 测试也不能冒充断电耐久性验证。无需为单元测试依赖特权 PID namespace；环境不支持
的集成项应报告具体缺项，不将假实现通过计作真实进程验证通过。

## 11. 相关契约

- [Pipeline 与 BuildRun](pipeline-contract.md)
- [Artifact Store](artifact-store.md)
- [资产身份与 provenance](asset-ir.md)
- [SAM 候选与选择](sam-instance-proposals.md)
- [场景到资产](scene-to-assets.md)
- [质量评估](evaluation.md)

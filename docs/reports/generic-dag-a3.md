# Generic DAG：进程执行与单图迁移切片

状态：后端与单运行浏览器审查切片已实现，已通过本轮 CPU/HTTP/前端回归；不宣称里程碑 A 完成。

## 实现

- BuildRun 保存 Artifact 到节点、attempt、端口、证据角色的反向索引。历史损坏阻塞其实际关联节点；无消费者的证据保存为运行级阻塞，不再任意归到首节点。索引只用于定位，不能替代引用闭包校验。
- 共享 gated worker 通过四个耐久回调记录准备、身份、授权和退出；授权写盘失败不会启动 Backend。既有工作台与 DAG 使用同一进程执行循环和跨运行准入检查。
- 每个 DAG attempt 保存多条 worker 记录。确认退出后持久化按完整身份寻址的终态证据，防止 PID 复用再次阻塞；不覆盖历史 attempt。
- DAG 子工作流复用 ChildRegistration/ChildRunContext，预留先于执行，成功记录精确子 BuildRun Artifact。恢复采用适配器的只读结果核实入口，不把松散模型输出当作成功。
- `dag-image` CLI 从 YAML 定义构建 SAM 候选 → 人工单对象选择 → TRELLIS2/TripoSR 发布。复用现有 profile 与独立环境；人工决定绑定原 request。CLI 的决定 JSON 属于显式操作者输入，不是自动质量审批。

## 验证边界

本轮已跑 CPU 子进程与 Fake Backend 验证。未运行真实 SAM/TRELLIS2 GPU 推理：检查时 GPU 利用率约 69–74%，未并行争用现有任务。未下载环境或权重、未重启现有服务。

首轮完整检查：828 passed，165.44 秒；Ruff 格式/lint、mypy（80 个源文件）和 sdist/wheel 构建通过。该轮覆盖后端/CLI切片，后续审查页面扩展需单独验收。真实 GPU smoke 与 SIGKILL 真实模型验证仍未完成。

浏览器审查新增单运行/指定人工节点模式，复用已有自动预览、点击mask与加载完成确认门控。服务端绑定request/final_mask/preview_signature并重算最终mask；确认在后台调用DAG决定，旧回执不再派发。它不是通用画布，未支持任意图多发布节点的结果布局。


## 仍未验收的范围

- 未在真实 GPU 模型上验证该 YAML 管线；当前 GPU 有持续外部负载，未抢占任务。
- CPU 故障注入不等同真实模型 SIGKILL 验收。
- Backend profile 当前按调用注册单图适配器，通用 YAML 任意 Backend 请求和 HTTP/ComfyUI 仍不开放。
- 审查页是指定运行与指定人工节点的模式；没有节点画布、自由连接与上传创建管线。
- 恢复可以采用已发布且完整验证的子运行；只有模型中间文件而 Core 未发布成功时仍需显式 retry。


## 最终检查

- 全量 `pytest -q`：833 passed，174.91 秒（含最终 UI 和故障回归）。
- `ruff format --check .`：157 文件通过；`ruff check .` 通过。
- `mypy src`：81 个源文件通过。
- `build --no-isolation`：sdist/wheel 构建成功；`git diff --check` 通过。
- HTTP 测试使用短期本机随机端口；未启动常驻服务。前端回归为 Node DOM harness，未声称真实浏览器手工验收。


## 恢复审查修正

- 子发布完成但父成功尚未落盘时，先验证已登记子 BuildRun 的完整证据闭包，再调用恢复适配器；损坏子证据参与父运行的阻塞及持久化检查。
- 恢复中的 mask、SelectionInputBinding 和 SelectionImportVerification 重算采用只计算身份的 Store 视图，禁止开启写事务，不会补回缺失 Blob。缺失输出仍由正常输出校验拒绝。
- 保存时仅要求新增 invalid_evidence 具备可核实的历史来源；已耐久确认的损坏事实不会因父证据随后损坏而被重新否定。无关的新 Artifact 仍不能加入豁免。
- 回归覆盖 selection/generation 子发布后父保存中断并删除证据、连续 P→Q 损坏，以及所有单图适配器恢复时禁止 Artifact 写事务。已更新操作指南的本地进程支持说明。

本轮最终全量：836 passed，211.58 秒；Ruff 格式/lint、mypy（81 个源文件）、sdist/wheel 构建和 `git diff --check` 均通过。未运行 GPU。

## 子运行索引恢复边界修正

- 恢复、历史损坏证据归属检查和父快照提交统一优先使用已固定的 child_result，不再要求可变 runs 索引仍然存在。
- 尚无固定结果时，索引缺失或 JSON 损坏会形成可持久化的节点 recovery_blocked；不会重建索引或触发子运行。
- 子运行 ownership 检查仍保留。保存阻塞时，只允许跳过已登记历史子运行的不可读索引，不由此引入新的 Artifact 豁免。
- 参数化回归覆盖索引正常/缺失/损坏、父结果已固定/保存前中断、Artifact 同时损坏，并重复恢复确认状态可持续保存。
- 真实模型推理中异常退出仍未验收；已完成的真实单图及完成后重启验证见 [真实 smoke 报告](generic-dag-real-smoke.md)。

本轮全量：847 passed，320.50 秒；Ruff 格式/lint、mypy（81 个源文件）、sdist/wheel 构建和 `git diff --check` 通过。

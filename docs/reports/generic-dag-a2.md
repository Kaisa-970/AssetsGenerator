# Generic DAG A2：CPU 与人工节点执行切片

状态：CPU／人工节点执行切片及独立 review 完成。不宣称完整 A2/A3 或 GPU/HTTP 编排已完成。

## 实现范围

- AdapterRegistry 将严格静态计划绑定到受信任 CPU/human 实现；参数 schema、默认值、实现模块摘要进入不可变 BoundDagPlan。未解析 Backend 请求拒绝派发。
- 按依赖执行扇出与汇合，实例不依赖特定 stage 名称；错误分支不阻止独立分支。
- DAG 扩展保存在 BuildRun 中，复用现有 Store、service lock、DurableIO/fsync 和 writer poison。旧 BuildRun 的空扩展不改变序列化。
- 输入摘要、输出 Artifact、provenance 与人工请求/决定耐久保存。当前恢复阻塞与原成功 attempt 分离。
- output identity 包含 run、节点实例、attempt、端口及集合元素，避免重复实例或重试的 provenance 碰撞。

此切片不能派发 GPU/process/HTTP Backend，因此不声称已经完成通用执行器对旧跨运行进程门控的集成；真实模型仍通过既有工作台执行。真正接入这些能力前必须完成对应 admission、worker registration、子运行和恢复测试。

## 可运行示例

见 [CPU 开发指南](../guides/generic-dag-core.md)。用仓库外目录实际运行 YAML 菱形图：A/B/C/D 各执行一次，B/C 同一输入 ArtifactRef，D 在两路成功后执行。另起 Python 进程恢复，四个节点 attempt 均保持 1，没有重复执行。此例只证明 CPU 执行与恢复，不证明模型质量。

## 验证结果

独立 review 驱动修复了以下具体问题：

- relation 反序列化时 list/tuple 差异导致汇合失败。
- 恢复必须重新核对适配器绑定、参数和已记录输入，不能仅校验输出存在。
- 人工请求/决定需校验 kind、schema、run/node/input 和 request 链接，不能只校验 digest。
- 已保存决定中断后可在相同输入下重试；上游输入改变或旧决定损坏时重新等待，不能静默沿用。
- provenance 恢复采用只读比对，不能通过重新写入“修复”损坏证据。
- 历史失败证据损坏也能耐久记录阻塞，不能让写阻塞状态本身失败。

历史失效记录暂采用保守策略：无法定位到当前消费者的坏历史证据由首节点承载阻塞原因；未使用的 Pipeline 输入损坏仍须修复。这是 CPU 首版的限制，不代表该首节点执行失败。真实 mask 审查 UI 尚未接入通用 human adapter。

验证使用现有虚拟环境，在 worktree 设置 `PYTHONPATH=src`：

- 全量 `pytest -q`：792 passed，141.93 秒。该进程在最终状态不变量及历史重复恢复测试收集前启动，不能声称这些后补用例包含在此数字中。
- 最终版本的 DAG engine/persistence/adapters/provenance/YAML 示例、旧工作台 foundation、历史摘要兼容测试：92 passed，15.61 秒；覆盖上述最后修正。
- 最终 `python -m build`：sdist 和 wheel 构建成功。
- `ruff format --check .`、`ruff check .`：通过。
- `mypy src`：74 个源文件通过。
- 独立 review 的最终脚本确认 node/attempt 状态不一致被拒绝、坏历史可恢复为阻塞、上游变化后人工节点重新等待。
- 未运行 GPU 或真实模型，未重启正在服务的工作台。

## 后续工作

先补通用 Core 对既有进程门控和跨运行准入的适配，以及真实单图步骤／固定 mask 人工组件迁移，再进入 A3 的真实 Backend smoke。不能把当前 CPU/Fake 验收等同完整 A2 或 A3。React Flow 和 HTTP/ComfyUI 仍留在后续里程碑。

## 本轮恢复审查修正

- 人工决定先耐久保存 `prepared` receipt，再发布内容寻址的决定 Artifact，最后保存 committed receipt。故障注入覆盖 Artifact 发布前、发布后但 committed 快照保存前；恢复与重放复用同一 ArtifactRef。
- 幂等键绑定 node/reviewer/payload，revision 仅用于新命令 CAS。已提交命令重放只返回现状，不重跑节点；中断执行由显式 retry 创建新 attempt 并重新验证输入，避免旧回执授权新的人工请求。
- retry 输入验证失败会保存 `retry_input_invalid` 阻塞证据后抛错；成功路径在 drain 重新加载前已保存 pending 状态。review 中“成功路径缺少 save”的描述与当前代码不符。
- 模型层拒绝没有原因的 recovery_blocked、其他状态附带 recovery reason、带 running attempt 的依赖 blocked，以及当前 attempt 与节点状态不一致；持久化禁止新增 attempt 时改写历史。
- 历史损坏证据的 artifact→consumers 索引仍是接入真实 Pipeline 前的待办，本轮没有宣称解决该限制。

独立复审另复现了过严 blocked 约束导致上游重试失败无法保存的问题，已允许 blocked 保留历史 attempt，并补充成功图上游重试失败后的落盘回归。旧幂等键不能确认上游变化后的新人工请求也有回归覆盖。

本轮最终定向测试（engine/persistence/adapters/provenance/YAML）：63 passed，100.99 秒。最终 Ruff 格式与 lint、mypy（74 个源文件）、`build --no-isolation`、`git diff --check` 均通过。本轮全量测试：805 passed，738.34 秒。全量进程在最终修正前已启动，因此不能用其数字代替上述最终定向验证，亦不将两组数字相加为新的全量结果。

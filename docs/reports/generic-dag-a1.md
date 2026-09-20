# Generic DAG A1：静态编译计划

状态：A1 静态编译实现与验证完成。本文只记录 A1，不宣称 Scheduler、任意图执行、React Flow 或远程 Backend 已实现。

## 本轮边界与设计取舍

`compile_pipeline()` 从只做校验扩展为返回不可变的静态 `CompiledPlan`。旧调用可以忽略返回值，继续沿用既有 Workflow。A1 的计划显式记录执行绑定尚未解析；不能直接传给运行器启动模型。

设计文档中的完整可执行计划需要 A2 的 AdapterRegistry 与执行绑定解析。A1 保存纯数据的 Operator 契约、节点实例、规范化参数与绑定、关系验证器身份和 Backend 请求。现有 `ResolvedPlan` 暂保留为旧 Workflow 的运行时绑定视图，其 implementation 对象不进入静态计划序列化，也不将旧 contract_digest 当作新计划身份。后续在通用执行入口完成绑定时生成新的完整计划身份，不能给不可变静态计划原地附加实现对象。

关系由 OperatorSpec 显式声明，registry 负责解析验证器实现。A1 提供最小版本化契约及验证入口，不宣称相机/深度等真实关系校验已经迁移。为兼容已有 YAML，旧编译路径允许未声明关系的多输入节点；新严格模式要求明确关系声明。计划记录采用的校验模式，后续通用执行入口不得将兼容模式误当作严格验收结果。

新增空 relations 字段不改变历史 Operator 契约序列化结果；非空声明参与身份。已有单图、多视图及工作台 Operator 集合摘要用回归测试保护。UI 交互修复和用户已修改的设计文档与本轮实现分开提交。

## 验证

新增 31 项编译计划测试、17 项关系验证器测试、3 项历史身份兼容测试。菱形图在 A1 只验证编译后的拓扑、共享绑定和依赖，不证明执行一次、失败传播或恢复；这些属于 A2。

独立 review 发现并修复：Operator 注册键与声明不一致、空间端口缺少静态保证、YAML 字符串被拆成多个 relation 端口，以及布尔契约字段接受字符串。解码会重编译并比较派生结构；即使重算摘要，伪造拓扑或依赖仍被拒绝。

在 worktree 使用主 checkout 的虚拟环境及 `PYTHONPATH=src`：

- 最终版本 `pytest tests/test_compiled_plan.py tests/test_relation_validators.py tests/test_compilation_compatibility.py tests/test_contracts.py -q`：84 passed。
- 全仓 `ruff format --check .`、`ruff check .`：通过。
- `mypy src`：69 个源文件通过。
- `python -m build`：sdist 和 wheel 构建成功。
- 全量 `pytest -q`：738 passed，160.80 秒。该进程在最后 6 项布尔字段回归新增前收集测试；随后最终版本的上述 84 项针对性检查已覆盖这 6 项。不将其声称为一次完整 744 项运行。

A1 不需要 GPU；本轮未运行真实模型，未重启工作台。

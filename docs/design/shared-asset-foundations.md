# 共享资产基础能力

本轮将既有函数从具体 workflow 移到三个独立模块，不修改执行器、持久化 schema、
身份公式或发布行为。新增调用者应使用下列公开入口。

| 模块 | 职责 | 边界 |
| --- | --- | --- |
| `mesh_io` | `load_scene`、`scene_vertices`；统一 GLB 读取与应用场景节点变换后的顶点读取 | 不生成模型、不做 canonicalization；保留原 `OperatorExecutionError` 类型兼容 |
| `provenance` | `output_id`、`persist_provenance`、`persist_build_run` | 现有 workflow 的输出身份与运行持久化，不替代 `dag_provenance` 的多 attempt 身份 |
| `release_io` | `release_files`、`materialize_json`、`materialize_release` | 校验已有 release 文件、暂存物化及原子发布；底层 no-replace 操作仍由 `publication` 提供 |

这些模块不依赖 `operators`、单图、多视图或场景 workflow。调用者迁移后保留局部旧名，
以维持现有故障注入测试；旧模块保留必要兼容导出，后续新增代码不从旧私有入口导入。

`persist_provenance` 保留旧的 `operator_version="1"` 和 `attempt=1` 规则。
不能将其用于通用 DAG 的重试身份；该区别是现有契约，不借重构统一身份算法。
组件元素标识仍进入输出身份，顺序和参数序列化规则不变。

本轮没有合并具有不同策略的发布器，也没有改变 release 路径校验的调用时机。
重复的多视图 provenance 调用目前依赖同一共享构造函数；各输出的证据列表仍在
workflow 明确列出，不抽成隐藏其来源差异的通用批处理。

兼容性验证包含：与迁移前函数体的 AST 比较、独立的 legacy provenance 字节与
Artifact identity 回归，以及现有材质、场景、发布竞争、故障恢复测试。

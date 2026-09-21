# 共享基础能力与状态文档整理

日期：2026-09-21；基线 `b6ee3e8`，改动在 node-workbench 工作树。

## 实现与审查

- 提取 `mesh_io`、`provenance`、`release_io` 三个独立边界，迁移现有调用者；
  具体边界见 [设计说明](../design/shared-asset-foundations.md)。
- 保留旧函数导入兼容；没有修改模型参数、持久化 schema、输出身份公式或 DAG 状态机。
- 对照 Git 基线逐项比较 8 个迁移函数的 AST，除符号重命名外函数体一致；
  其他调用者去除 import 后 AST 一致。此次为主 agent 自审，没有独立 subagent review。
- AST 统计相对导入私有名称的语句从 81 减至 56；其中从 workflow/operators/
  scene_workflow 导入私有名称的语句从 29 减至 4。未将剩余所有私有函数一并重构。
- README 推荐中文节点工作台指南；B2 当前状态保留短表，历史验证移入独立归档。
  ComfyUI 真实部署验收明确延期；没有删除旧 CLI 或扩展模型/界面。

## 验证

第一次测试命令未设置工作树 PYTHONPATH，误用主目录的 editable 安装，82 项收集错误；
该次不计为实现验证。随后指定 `PYTHONPATH=src` 的完整测试：1348 passed、3 failed，
759.12 秒。三项均为故障注入仍引用已迁走的 `workflow.shutil.copyfile`。
测试改为在 `release_io.shutil.copyfile` 注入，保留发布失败、暂存清理和失败运行断言。

修正故障注入路径后，单图/多视图 workflow 与新增基础测试合跑 **75 passed（16.51 秒）**。
没有重复完整套件，不将分轮结果相加表述为一次全量通过。

新增四项检查覆盖 legacy provenance 序列化字节、Artifact identity 与共享模块依赖方向。
编写过程中修正了 fixture 使用未知 kind 和漏列历史默认字段的问题；最终四项通过。

Ruff lint/format、mypy（135 源文件）、sdist/wheel 构建和 diff 检查通过。
未修改前端，未重跑前端测试。真实 DA3 推理主控中断、串行重试与发布验收见
[专项报告](multiview-interruption-real.md)，不等同质量 benchmark。

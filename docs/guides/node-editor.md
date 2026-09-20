# 节点编辑器 B1

首版编辑草稿，不派发模型。模型执行仍使用现有 DAG CLI；执行按钮、人工节点组件与运行管理将在 B2 接入。

## 启动

首次或前端改动后，在仓库 worktree 构建静态资源：

```bash
cd frontend
npm ci
npm run build
cd ..
PYTHONPATH=src <MAIN_CHECKOUT>/.venv/bin/python -m assets_generator.cli node-editor \
  --directory <RUN_ROOT>/drafts --port 8767 \
  --operators examples/dag-multi-view-operators.yaml \
  --template examples/dag-image-asset.yaml \
  --template examples/dag-multi-view-asset.yaml
```

打开打印的本机地址。运行时不使用 CDN、不加载模型环境。静态资源由 Python 服务托管；生成的 node-editor 资源被 Git 忽略，构建 wheel 前必须先 npm run build，wheel 会包含资源。npm ci 只安装前端依赖。

## 操作

- 从左侧选择模板，或点击/拖放算子到画布。添加管线输入并编辑其端口契约。
- 从输出端口拖到输入端口；同一算子可放多个实例，实例 ID、参数和绑定分别保存。
- 选择节点，在右侧编辑实例名称、参数 JSON、adapter/backend。默认目录只包含 OperatorSpec；尚无安装的模型 registry 时，填写绑定仅记录请求。
- 点击编译校验。后端重新编译并要求显式汇合关系；静态通过不等于 Backend 已验证或可运行。应用注入 AdapterRegistry 时，会校验注册适配器参数和绑定，但页面仍不执行。
- 保存名称使用字母、数字、下划线、连字符。草稿保存 pipeline 和独立 layout，未完成/不合法的草稿也可保存；保存时附带编译结果。
- YAML 导入导出不包含 UI 布局；布局只存在草稿文件。同名节点状态可从 BuildRun JSON 只读展示，标签是历史记录，不表示当前草稿已运行。

后端可定位多数 node.port 契约错误；环或全局汇合错误可能只有全局提示。编译结果是只读反馈，修改图后必须重新校验。

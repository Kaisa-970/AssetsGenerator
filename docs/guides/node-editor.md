# 节点编辑器

默认模式编辑和编译草稿；配置本地模型 profile 后，可从画布执行单图 DAG，并打开现有 mask 审查页面。B2 首片只支持一个服务配置的 profile 和一个 image 输入。

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

打开打印的本机地址。默认静态模式不加载模型环境，前端不使用 CDN。静态资源由 Python 服务托管；生成的 node-editor 资源被 Git 忽略，构建 wheel 前必须先 npm run build，wheel 会包含资源。npm ci 只安装前端依赖。

## 操作

- 从左侧选择模板，或点击/拖放算子到画布。添加管线输入并编辑其端口契约。
- 从输出端口拖到输入端口；同一算子可放多个实例，实例 ID、参数和绑定分别保存。
- 选择节点，在右侧编辑实例名称、参数 JSON、adapter/backend。默认目录只包含 OperatorSpec；尚无安装的模型 registry 时，填写绑定仅记录请求。
- 点击编译校验。后端重新编译并要求显式汇合关系；静态通过不等于 Backend 已验证或可运行。应用注入 AdapterRegistry 时，会校验注册适配器参数和绑定，启用执行时同时校验注册的适配器和参数。
- 保存名称使用字母、数字、下划线、连字符。草稿保存 pipeline 和独立 layout，未完成/不合法的草稿也可保存；保存时附带编译结果。
- YAML 导入导出不包含 UI 布局；布局只存在草稿文件。同名节点状态可从 BuildRun JSON 只读展示，标签是历史记录，不表示当前草稿已运行。

后端可定位多数 node.port 契约错误；环或全局汇合错误可能只有全局提示。编译结果是只读反馈，修改图后必须重新校验。

## 单图执行（B2 首片）

复用已有本地模型配置和环境，启动时增加三个参数：

```bash
PYTHONPATH=src <MAIN_CHECKOUT>/.venv/bin/python -m assets_generator.cli node-editor \
  --directory <RUN_ROOT>/editor --store <RUN_ROOT>/store \
  --config <LOCAL_PROFILE_CONFIG>.json --profile trellis-local \
  --template examples/dag-image-asset.yaml --port 8767
```

加载单图模板，在运行面板填写**服务所在机器的图像路径**，然后启动运行。编译通过但不满足当前入口输入限制时，页面会显示不可运行原因；单图入口要求恰好一个名为 image 的 RGB 图像输入。启动时后端重新编译当前图，导入图像并固定不可变计划；之后编辑草稿不会改变该运行。profile 在服务启动时加载并核验本地资源身份，首次启动可能需要等待数十秒或更久，不会重新下载 PyTorch 或模型。

人工节点等待后，点击审查入口，在独立本机页面预览 mask、填写检查人并确认；确认后同一个 DAG 继续执行。画布页可以查看状态、错误、输出链接，并显式恢复或重试。关闭编辑器服务会等待当前工作线程结束；强制退出后的进程检查和恢复仍由既有 DAG 门控负责。刷新页面可从运行列表重新打开记录，读取状态不会自动恢复或重跑。

运行创建目前没有客户端幂等键；若启动响应丢失，先刷新运行列表确认是否已经创建，不要直接重复启动。恢复和重试使用当前 revision，失败时先重新读取状态。服务串行接收执行命令，人工决定执行期间不能启动另一条任务。

边界：模型 profile 属于服务配置，尚未实现每个节点独立选择不同 profile；输入暂为本地图像路径，尚无浏览器上传。参数仍使用 JSON 表单；多视图执行、HTTP Backend、ComfyUI 尚未接入画布。GLB 输出可通过链接读取，现有人工审查页可查看模型。

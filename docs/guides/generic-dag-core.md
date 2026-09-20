# 通用 DAG Core：CPU 开发示例

此入口面向开发验证，不是 React Flow 编辑器。当前支持受信任的 CPU、人工与受控本地进程适配器，单图模型流程已接入 YAML 和审查入口，但尚未完成真实 GPU 验证。HTTP 和 ComfyUI 尚未接入，现有固定工作台继续使用原流程。

## 运行 YAML 菱形图

在开发 worktree 中运行，复用已有虚拟环境：

```bash
PYTHONPATH=src <MAIN_CHECKOUT>/.venv/bin/python examples/dag_diamond_demo.py \
  --directory <RUN_ROOT>/dag-demo
```

输入是脚本生成的小型文本 Artifact，不读取照片、不下载模型、不占 GPU。输出目录应在仓库外。图由 `examples/dag-diamond.yaml` 和 `examples/dag-operators.yaml` 定义：

```text
A → B → D
 \→ C →/
```

运行打印四次 `execute`、`shared input: True` 及一个 `dag_...` 运行 ID。A/B/C 透传同一 ArtifactRef，D 合并两个输入的内容。该示例的 join 显式声明输入可独立；它不替代相机/深度的关系校验。

用打印的运行 ID，在另一个 Python 进程恢复：

```bash
PYTHONPATH=src <MAIN_CHECKOUT>/.venv/bin/python examples/dag_diamond_demo.py \
  --directory <RUN_ROOT>/dag-demo --recover <RUN_ID>
```

成功恢复只打印状态，各节点仍只有一次 attempt，不应再打印 `execute`。保留同一个 Store 和运行目录；改 adapter 源码后恢复可能因实现身份变化而拒绝，这是证据检查的结果，不应通过改历史计划绕过。

## 开发接口与边界

先用 `compile_pipeline(..., require_explicit_joins=True)` 生成静态计划，再用 `AdapterRegistry.bind_plan()` 解析实际适配器、参数默认值和实现身份。`DagEngine.create()` 创建运行，`drain()` 串行执行就绪节点，人工等待不阻止独立分支。`recover()` 校验记录，`retry()` 显式重试；决定通过 `decide()` 绑定当前请求及 revision/idempotency key。`save_draft()` 只保存未确认编辑，不授权下游执行；相同输入的已确认决定可在同一运行中断后重试，输入变化则重新建立人工请求。

`BuildRun.dag` 保存实际输入和权威节点状态；通用 `node_attempts` 是便于既有诊断读取的投影。历史成功 attempt 保留，当前状态可因证据无效进入 `recovery_blocked`。旧工作台记录仍使用其原 schema。

Adapter 是服务端受信任 Python 代码，不是安全沙箱。参数必须显式声明；实现身份覆盖适配器类所在模块源码，不自动证明所有依赖或隐藏实例状态相同。不得把模型调用隐藏在 CPU adapter 中规避独立环境和门控要求。真实模型迁移需要后续单独验收。

## 单图 YAML 入口（开发切片）

新增 `dag-image` 入口复用固定工作台的 profile JSON（参见 [配置说明](node-workbench.md)），支持决定 JSON，以及下文单运行浏览器审查入口。无需重新安装 PyTorch。

```bash
PYTHONPATH=src <MAIN_CHECKOUT>/.venv/bin/python -m assets_generator.cli dag-image start \
  --config <RUN_ROOT>/config.json --profile trellis-local \
  --store <RUN_ROOT>/dag-store --directory <RUN_ROOT>/dag-service \
  --pipeline examples/dag-image-asset.yaml --image <INPUT_IMAGE>
```

响应包含 run_id、dag.revision 和各节点的精确输出。候选完成后停在 `choose_object`；人工选择后写入决定文件，例如：

```json
{"proposal_id":"<实际候选ID>","invert":false,"keep_largest":true}
```

使用同一配置、Store、directory：

```bash
PYTHONPATH=src <MAIN_CHECKOUT>/.venv/bin/python -m assets_generator.cli dag-image decide \
  --config <RUN_ROOT>/config.json --profile trellis-local \
  --store <RUN_ROOT>/dag-store --directory <RUN_ROOT>/dag-service \
  --run <RUN_ID> --node choose_object --expected-revision <REVISION> \
  --key <UNIQUE_DECISION_KEY> --reviewer <REVIEWER> --decision <DECISION_JSON>
```

`resume --run <RUN_ID>` 恢复并继续 ready 节点；`retry` 另需节点和最新 revision。重复决定只核对回执，中断执行通过显式 retry 继续。不要将新决定复用旧 key。`generate_asset` 输出 asset/release/glb/qa ArtifactRef，实际发布目录位于服务目录 `executions/<RUN_ID>/<CHILD_ID>/release`。

此入口尚未通过真实模型 GPU smoke，不应据 CPU/Fake 测试宣称真实模型验证完成。


浏览器审查入口复用自动预览与图片点击选择：

```bash
PYTHONPATH=src <MAIN_CHECKOUT>/.venv/bin/python -m assets_generator.cli dag-image review \
  --config <RUN_ROOT>/config.json --profile trellis-local \
  --store <RUN_ROOT>/dag-store --directory <RUN_ROOT>/dag-service \
  --run <RUN_ID> --node choose_object --port 8766
```

服务仅绑定本机，选择对应的已有候选，检查预览后填写检查人并确认。请为 DAG 使用独立端口；同一个 service directory 同时只能由一个进程持有，浏览器服务运行时不要再用另一 CLI 写该目录。具体 UI 验证状态见 A3 报告。

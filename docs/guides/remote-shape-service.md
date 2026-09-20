# 本机远程 shape 服务实验入口

当前供开发验收使用。已完成 [TRELLIS.2 远程 shape GPU smoke](../reports/remote-shape-real-smoke.md)，尚未接编辑器服务目录；五节点完整资产链另见 [真实运行报告](../reports/remote-asset-chain-real-smoke.md)。这些 smoke 不代表质量验收。复用已有 TripoSR/TRELLIS2 环境和模型，不安装 PyTorch 或下载权重。

配置 JSON 只包含 `profiles`，每个 profile 的字段沿用 workbench shape 配置（backend/python/repo/model，以及可选模型参数）；不包含 `sam`。所有资源路径指向现有本地资源，TripoSR 仍要求 frame_validation。

在 worktree 中显式设置 `PYTHONPATH=src`，使用项目 Python：

```bash
PYTHONPATH=src python -m assets_generator.remote_shape_cli serve \
  --config <CONFIG_JSON> --profile <PROFILE_NAME> --service-id shape-local \
  --database <RUN_ROOT>/service.sqlite --workspace <RUN_ROOT>/workspace --port 8770
```

启动会核验模型、代码和环境摘要，向 stderr 输出进度；stdout 打印 endpoint、service_id、backend_digest。客户端通过受信 RemoteShapeAdapter 绑定这些值。HTTP POST 只登记 queued，不会自动执行模型。Ctrl+C 关闭监听，不取消已在其他进程执行的作业。

另一终端使用相同配置、service-id、数据库和 workspace，将动作改为 `inspect --job <SUBMISSION_KEY>` 查询，或 `execute --job <SUBMISSION_KEY>` 显式执行一个 queued 作业。成功/失败输出 JSON；execute 对失败返回非零，重复执行不会重跑终态或 running 作业。

`observe --job <SUBMISSION_KEY>` 重新探测原进程身份。只有确认进程组退出才释放同数据库占用，不自动改写原作业状态或补跑。缺失身份仍阻塞。每个命令当前都会重新核验 profile，模型较大时启动较慢。

同一数据库只允许一个未确认退出的模型进程；不同数据库之间尚无 GPU 互斥。当前仅监听 127.0.0.1，无公网认证，不应通过反向代理开放公网。Store、数据库、模型、输入和输出均放仓库外。


模块化资产链示例为 `pipelines/remote_shape_asset_v1.yaml`，输入是已确认的 RGBA Artifact。
客户端可信 registry 需注册 `RemoteShapeAdapter(endpoint, identity)`、`CanonicalizeAdapter()`、
`GeometryValidationAdapter()`、`ShapeAssetAssemblyAdapter()`、`AssetExportAdapter()`。
用 `load_pipeline()` 加载 YAML，`compile_pipeline(..., require_explicit_joins=True)` 编译，
随后 `registry.bind_plan()`、`DagEngine.create()` 和 `drain()`。

首次 drain 提交服务作业并保持 running。服务端 execute 完成后再次 drain，Core 才会继续
规范化、QA、组装和导出。`publish.outputs.release` 是 Store 内 AssetRelease，
`publish.outputs.glb` 是可读取的 GLB Artifact；尚未自动生成下载目录。
关闭 HTTP 服务后，对已成功运行调用 recover 应仅核验本地证据，不重新推理。

## 编辑器中的可信服务目录

`node-editor --remote-config <REMOTE_JSON> --store <STORE> --directory <EDITOR_DIR>`
可独立启动远程节点目录，也可与已有 image/multi-view 配置同时使用。
该配置由启动者提供，加载时不连接服务、不加载本地模型环境：

```json
{
  "default_profile": "shape-local",
  "profiles": {
    "shape-local": {
      "endpoint": "http://127.0.0.1:8770",
      "service_id": "shape-local",
      "backend_digest": "sha256:<服务启动时输出的64位摘要>"
    }
  }
}
```

服务身份使用 `serve` 输出的实际值；每个 profile 注册为可选 Backend。
节点 `backend: <profile-name>` 选择服务，不指定则使用 default_profile。
endpoint/service_id/backend_digest 固定在参数 enum 中，草稿不能任意改写。
规范化、QA、组装、导出四个 Core Adapter 同时进入目录。

编辑器支持 prepared RGBA 五节点图的编译与启动。使用 `--template pipelines/remote_shape_asset_v1.yaml`
载入示例；上传已处理好的 RGBA PNG，或填写服务所在电脑的文件路径。RGBA 保留透明通道，
拒绝全透明输入；不会自动抠图。已有 Artifact 引用同样检查编码、身份和非空 alpha。

远程服务 POST 仍只登记作业；需由服务端显式 execute。作业完成后在画布执行恢复命令，
继续 Core 节点；页面状态轮询不派发模型。这部分已有 CPU API 测试，尚未做浏览器真实模型全链验收。

浏览器 Playwright 回归覆盖 RGBA 专用上传路由、明确点击后创建运行、提交精确 ArtifactRef，
以及切换成 RGB 图后禁止复用旧 RGBA 上传。该测试使用模拟 API，证明界面请求行为，
不替代真实服务端图像校验或浏览器真实模型全链验收。

真实 HTTP 集成回归 `tests/test_node_editor_remote_http.py` 启动编辑器和远程服务，
通过 HTTP 完成 compile → RGBA 上传 → 幂等创建 → 服务端执行 → 显式 resume → GLB/Release 读取。
它验证 GET 轮询不消费远程成功结果、重复创建不产生新运行、五节点各一次 attempt、GLB 可回读。
该测试使用独立 CPU 测试 Backend；与浏览器模拟 API 测试、真实 TRELLIS.2 DAG smoke 是不同证据，
不能合称已完成“浏览器直接操作真实模型”的验收。

远程节点运行面板展示上次保存的 queued/running/unknown 提示、服务名称和可复制的 submission key。
把该标识传给服务端 `execute --job`。提示不是实时服务状态；点击“恢复 / 继续此运行”才会核实
原作业并继续后续节点，普通页面轮询不会派发或重复提交。

运行面板按 Artifact kind 列出 GLB、AssetDefinition、AssetRelease 和 QualityReport，
因此 `quality.report` 也可直接查看。读取前验证证据闭包；报告节点成功只说明报告生成成功，
质量结论以 JSON 内 `overall_status` 和各项检查为准。中间 triangle_mesh 不作为交付 GLB 展示。

## 有界串行消费

服务端可使用 `drain` 代替逐个复制 job ID：

```bash
PYTHONPATH=src python -m assets_generator.remote_shape_cli drain \
  --config <CONFIG_JSON> --profile <PROFILE_NAME> --service-id shape-local \
  --database <RUN_ROOT>/service.sqlite --workspace <RUN_ROOT>/workspace --max-jobs 10
```

配置核验一次，按登记顺序串行执行最多 10 个 queued 作业，每完成一个输出一行 JSON。
队列为空立即退出；失败返回非零并停止，不跳过失败继续批量推理。已有任意 running 作业时
不领取新作业，不改写其状态；需要先核实原作业。领取由 SQLite 事务保护，多个 drain
连接不会同时领取任务。它不是常驻轮询守护进程；新任务需要再次执行 drain。
进程门控仍生效，不同数据库之间仍无统一 GPU 调度。本命令仅 CPU 回归验证，未重新跑 GPU。

`execute --job` 与 `drain` 使用同样的数据库级空闲准入：若另一作业仍 running，
显式执行也在领取阶段拒绝，目标作业保留 queued，不调用 handler。底层模型进程门控继续作为
第二层保护；这不自动消除历史中断作业，也不跨数据库调度。

服务端 `list` 不要求 `--job`，其余配置参数与 serve 相同。`--limit 100` 限制返回条数
（1–1000），输出 `jobs`（job_id/state/error）和 `next_before`。有下一页时传
`--before <next_before>`，按登记顺序从新到旧浏览。分页游标不受新增任务影响；每页是当时
保存的状态，不是整个列表的跨页事务快照。读取不探测进程、不恢复、不领取任务。
当前命令仍先核验 profile，启动成本与 inspect 相同。

# 本机远程 shape 服务实验入口

当前供开发验收使用。已完成 [TRELLIS.2 远程 shape GPU smoke](../reports/remote-shape-real-smoke.md)，以及 [真实 SAM 选区到远程 TRELLIS.2 发布链](../reports/remote-selected-trellis-fallback-real-smoke.md)；编辑器可通过 `--remote-config` 加载可信服务目录并按节点选择。这些 smoke 不代表质量验收。复用已有 TripoSR/TRELLIS2 环境和模型，不安装 PyTorch 或下载权重。

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

## 显式放弃已退出作业的未发布结果

若作业因服务中断一直 running，且决定放弃该次未发布结果，可使用
`abandon-exited --job <SUBMISSION_KEY>`（其他配置参数同 execute）。该命令先核实保存的
进程身份，只有进程组已确认 exited 才将 running → failed，错误码为
`SERVICE_RESULT_ABANDONED`。不杀进程、不补写成功、不伪造 exit code；原 worker 和退出证据保留。
即使进程退出码为零，也只能代表用户明确放弃结果，不能推断已发布成功。

alive/unknown/缺失身份一律拒绝；已有 succeeded/其他 failed 不覆盖。重复提交相同 abandon
返回原失败结果。与正在发布的成功结果竞争时，终态 CAS 决定结果，不覆盖已成功发布的作业。
随后画布恢复会读取这个失败，重试需显式触发；其他排队作业可再次 drain。
目前没有进程证据的中断（例如领取后尚未登记 worker）仍保持阻塞，不提供强制清除入口。

真实 HTTP CPU 集成测试也覆盖 abandon → Core resume 固定失败结果 → 显式 retry 新建 submission key
→ 成功发布。原失败 attempt 与 remote_result 完整保留，shape 两次 attempt，下游各一次；
原服务作业保持 failed，新作业 succeeded。此测试模拟进程退出后终态未发布的窗口，
不宣称已验收真实 GPU 进程中途被杀的场景。

## 从普通照片和人工 mask 开始

`pipelines/remote_selected_image_asset_v1.yaml` 使用 RGB 输入，串接已有 instance proposals、
人工选区、`selection_prepare@1`、远程 shape 及五节点发布后半段。编辑器需同时传入本地
`--config/--profile`（提供 SAM proposals）和 `--remote-config`，并通过 `--template` 加载示例。
可改用 `--proposal-config` 与 `--profile`，只加载 SAM 资源，不配置或核验本地 Shape。

人工确认之前无远程提交。prepare 从 SelectionInputBinding 读取原图和最终 mask，复用
绑定验证与 binary-mask 校验，输出 RGBA、ObservationBundle 和 SelectionImportVerification。
这三个输出及 provenance 保留在 DAG。示例将 observations 显式连接到 shape assembly：
Core 核验单视图 RGB/mask 与实际 RGBA 内容一致后，将 observation_id 写入资产，
精确 ObservationBundle Artifact 保留在组装输入和 provenance。该可选输入不提供时，
旧 RGBA 管线仍不推断观测来源；损坏证据不会由比较过程重新生成。

CPU 集成测试覆盖人工等待/决定、alpha 保留、精确 RGBA 输入、HTTP shape 发布及服务关闭后恢复。
此示例已完成真实 SAM + 远程 TRELLIS.2 同次运行验收；当前运行因 TRELLIS.2 `CuMesh` 后处理失败采用无纹理几何 fallback，详见真实运行报告。服务目录、编辑器内逐节点服务选择和完整纹理后处理仍待完成。

selection_prepare 在任何转换前验证绑定的完整引用闭包，并用已有 selection、原 proposals 和
MaskDraft 在内存中重建预期绑定身份；仅比较图片/mask 自报字段不足以通过。缺失历史 mask
会拒绝且保持缺失，不会因验证重算而重新写回 Store。

### 直接启动“照片 → 选区 → 远程生成”编辑器

先启动远程服务并把 stdout 中的 `service_id`、`backend_digest` 写入
`examples/remote-selected-editor.json.example` 的副本。再准备本地 image profile（其中
`backend`、SAM/shape Python 和模型路径指向现有环境），然后运行：

```bash
cp examples/remote-selected-editor.json.example "$RUN_ROOT/remote.json"
# 编辑 $RUN_ROOT/remote.json，替换 <...> 占位符
PYTHONPATH=src python -m assets_generator.cli node-editor \
  --directory "$RUN_ROOT/editor" \
  --store "$RUN_ROOT/store" \
  --config "$IMAGE_CONFIG" --profile "$IMAGE_PROFILE" \
  --remote-config "$RUN_ROOT/remote.json" \
  --template pipelines/remote_selected_image_asset_v1.yaml \
  --port 8767
```

打开画布后选择该模板，点击“运行”，上传 RGB 照片或填写服务器本地路径。运行会先在
选区节点等待；完成人工选择后才创建远程 shape 作业。服务端可用 `drain` 消费队列，
然后在画布点击“恢复 / 继续此运行”。发布结果和 `quality.report` 会出现在运行输出中。
本地 image profile 目前会加载其声明的资源摘要；首次启动可能较慢。

模块化发布会在 `release.files` 中加入当前运行、对应精确资产的组装 provenance。
因此从 Release 的 Artifact 引用闭包可以追溯组装输入中的 ObservationBundle，
而不是把 `source_observation_ids` 中的语义 ID 当作 Artifact ID。
这一行为已有 Release 单独校验与观测 Blob 丢失回归；不追溯修改旧发布。

### 只加载本地 SAM，生成交给远程服务

复制 `examples/sam-proposals.json.example` 到仓库外，填写已有 SAM 环境和 checkpoint。
配置不包含 Shape 模型；`--proposal-config` 与原 `--config` 互斥。

```bash
PYTHONPATH=src python -m assets_generator.cli node-editor \
  --directory <RUN_ROOT>/editor --store <RUN_ROOT>/store \
  --proposal-config <RUN_ROOT>/sam.json --profile sam-local \
  --remote-config <RUN_ROOT>/remote.json \
  --template pipelines/remote_selected_image_asset_v1.yaml
```

该路径注册 proposals、人工选择和远程生成所需算子，不提供本地 image_build。
已有固定工作台和完整本地 profile 入口继续保留。SAM-only 配置和远程发布组合已有 CPU
回归；此新配置入口尚未重复进行真实 GPU 验收。

### 同一输入对比两个远程 Backend

`examples/remote-shape-compare.yaml` 将同一个已准备 RGBA 输入连接到两个 shape 节点，
各自执行 canonical、QA、assemble、publish。把 `first-service`、`second-service` 改为
受信 remote 配置中的名称，再通过 `--template` 加载；每一路都有独立 Release。
服务端作业仍需显式执行；若共享 GPU，请串行执行，Core 不提供跨服务 GPU 互斥。

双 HTTP 服务的 CPU 回归验证了同输入、独立身份与作业、分支完成状态和关闭服务后恢复；
另已直接执行对比模板的 10 个节点，验证两份独立 AssetDefinition/Release、各自组装
provenance 和服务关闭后的离线恢复；每节点一次 attempt。测试 Backend 使用小型 CPU
几何输出，尚未对两个真实模型运行此对比模板，不构成质量排名。

### 可复用浏览器提交验收

先使用 `frontend/smoke/editor_server.py --root <EMPTY_CPU_ROOT> --remote-submit`
启动 CPU HTTP fixture（`PYTHONPATH=src:tests`），再执行：

```bash
node frontend/smoke/embedded-review.cjs <EMPTY_CPU_ROOT>/browser-config.json --remote-submit
```

真实编辑器可使用同样脚本，但需自己提供 JSON 中的 `url`、`image`、`root`、
`template: "remote_selected_image_asset_v1"`。脚本会创建运行并执行分割、自动选择第一个候选，
以 `Codex automated browser smoke (not user approval)` 提交决定。仅供自动化验收，不能当作
用户选区批准。默认输入为本地路径；`--upload-retry` 另外覆盖上传和创建响应丢失重试。

`--remote-submit` 模式在远程作业登记后结束，独占写入 `browser-submitted.json`，
保留精确父运行和作业标识及截图。脚本不会调用服务 execute，不宣称生成或发布成功。
本轮 CPU 路径已实际通过，证据位于 `<DATASET_ROOT>/browser-remote-submit-cpu-v1/`；
未加 upload-retry，未执行 GPU。真实推理及发布恢复需后续单独验证。

浏览器脚本在启动浏览器之前独占并耐久写入 `browser-smoke-started.json`。
已有提交结果、完成结果或启动标记时拒绝重跑，即使前次失败也不自动删除标记。
先检查原运行和服务作业；另一次独立验收应使用新的目录。单元验证命令为
`node --test frontend/smoke/claim-run.test.cjs`。

## 可复用浏览器继续验收

`frontend/smoke/embedded-review.cjs <BROWSER_CONFIG> --remote-submit` 通过页面创建运行、
确认一个候选并保存 `browser-submitted.json`。自动检查人明确标记为
`Codex automated browser smoke (not user approval)`；不代表用户批准或 mask 质量结论。
配置包含 `url`、`root`、`image` 和 `template`，root 必须为新的仓库外运行目录。

在服务端执行已保存的 submission key 后，运行：

```bash
node frontend/smoke/remote-complete.cjs <BROWSER_CONFIG>
```

脚本只选择已提交运行并点击恢复，不创建运行、不重新提交决定、不启动服务端模型。
它检查全部节点成功且各一次 attempt、原远程绑定和决定回执不变、发布 GLB/Release
存在且全部展示输出可下载、GLB 文件头与长度有效，然后再次通过页面恢复并比较
节点、回执和输出。结果与截图写入 `browser-completed.json/png`；JSON 禁止覆盖。
文件头检查不能替代渲染或几何质量验收。

2026-09-20已在真实浏览器和 HTTP 服务上用 CPU fixture 跑通上述两个脚本。
运行 `dag_474fa726596740c0b73fbd751edc22b7` 的外部证据位于
`<DATASET_ROOT>/browser-remote-complete-cpu-v1/`。这是浏览器操作、服务边界和恢复验收，
SAM/TRELLIS.2 均未在本次执行，不能作为真实模型全链证据。

真实 SAM-only 与远程 TRELLIS.2 已用以上浏览器脚本完成正常全链，
见 [2026-09-21 验收报告](../reports/sam-only-browser-real-complete.md)。
输出使用无纹理几何 fallback；不表示完整纹理质量或推理中异常恢复通过。

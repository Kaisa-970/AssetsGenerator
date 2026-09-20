# 节点编辑器 B2 单图执行首片

本轮在 B1 草稿编辑器上接入现有 DagEngine，不修改 Scheduler，也不替换固定工作台。服务启动时选择一个已安装的本地 profile；从当前草稿重新编译、绑定并创建不可变计划和 BuildRun，后台串行执行。

## 已实现

- 本地图像路径输入、运行创建和列表、只读状态轮询、显式恢复及 revision 校验的重试。
- 复用独立 mask 审查页面；决定仍由用户明确提交，保留原决定幂等和持久化语义。
- 服务仓储所有权隔离；输出仅通过成功节点的明确端口读取，并验证证据闭包。
- 活跃工作线程与人工决定互斥。无效或过期命令不应关闭现有审查页面。
- 前端区分可编辑草稿与固定运行，运行状态目前显示在右侧运行面板；没有把状态冒充为当前草稿画布的执行结果。
- 请求失败不自动重发，刷新运行列表可检查响应丢失后的实际结果。

## 验证与范围

前端 TypeScript/Vite 构建、9 项 Vitest、5 项 Playwright 已通过。浏览器回归含运行 API 状态展示、revision 操作和创建响应丢失时不自动重复提交。HTTP 测试覆盖 Host/Origin、请求字段、错误类型及输出路由。

新执行服务使用 CPU/Fake Backend 验证固定计划、异步状态读取、人工决定和发布输出，不构成真实模型质量或 GPU 验收。首次组合测试中 3 个新增异步测试因 10 秒等待上限失败；调整轮询间隔和合理超时后单独复验，3 项执行服务测试通过（62.68 秒）；最终 HTTP/CLI 组合 11 项通过。Ruff、mypy 和 sdist/wheel 构建通过。不将不同轮次结果相加声称全仓通过。

首片提交时尚无新的画布真实 GPU 端到端验收，参数表单、运行图与内嵌审查也尚未接入；后续进度见下文。完整 B2 仍未关闭，逐节点多 profile 选择仍待实现。多视图执行、HTTP/ComfyUI 仍未接入画布。创建新运行没有幂等键，响应丢失时需先核实列表，不能盲目重发。

操作见 [节点编辑器指南](../guides/node-editor.md)。外部配置和运行目录使用 `<RUN_ROOT>`，不提交模型路径配置、输入或 Store。

实际本地 profile 启动检查在约 5 分钟后仍处于 TRELLIS 模型 snapshot digest 计算，已主动终止该检查进程，未监听 B2 端口、未启动推理。因此不宣称真实配置服务启动验收通过；原 B1 服务未受影响。

提交前补齐执行入口适用性：编译与启动共用输入检查，输入名改为 photo 时仍可编译，但 execution_ready=false 并提供 execution_reason；前端显示原因并禁用启动。Python 回归核对编译与启动拒绝原因一致，浏览器回归验证提示与禁用状态。

本次输入限制修复后的检查：9 项 Python 编辑器/执行服务测试、9 项 Vitest、5 项 Playwright 通过；TypeScript/Vite 构建、Ruff check/format、mypy（85 个源码文件）及 git diff --check 通过。未重复运行全仓测试或真实模型推理。

后续真实配置启动、画布单图流程及完成后服务重启验收已完成，见 [B2 真实运行报告](node-editor-b2-real-smoke.md)。此前失败/中止的启动尝试保留为历史事实；本结果仍不关闭完整 B2。

后续参数表单首片：从 Adapter schema/defaults 生成基础字段，保留 JSON 入口及后端权威校验。新增浏览器回归验证整数拒绝小数、类型保持与多实例隔离；6 项 Playwright、TypeScript/Vite 构建通过。嵌套对象/数组的专用控件仍未完成。

固定运行图首片：新增受运行目录所有权限制的只读 plan API，复用计划证据闭包和绑定校验；前端检查计划 ID 与所选运行一致，展示不可编辑的节点/依赖及持久状态。9 项 Python 编辑器测试、7 项浏览器测试、TypeScript/Vite、Ruff、mypy 通过。新增浏览器路径证明修改草稿后仍显示原计划，并在关闭视图后保留草稿；未为此重新执行 GPU 推理。

运行图交互加固：固定运行图使用独立 ReactFlowProvider，避免共享草稿画布的内部节点和视口状态。浏览器回归包含非空草稿，打开运行图、缩放后核对草稿节点与视口未变，关闭后原草稿仍可见。

内嵌审查入口：工作台使用 iframe 复用原 DagMaskReviewService 页面，未新增决定语义或自动批准。浏览器测试覆盖加载、收起，以及这些 UI 操作不派发决定/恢复请求；本轮未重新做真实 GPU 验证，真实单图记录仍见专项报告。

内嵌审查验证结果：7 项 Playwright 与 TypeScript/Vite 构建通过。跨端口 iframe 测试使用临时本机 HTTP 服务实际加载页面，覆盖收起不发送决定请求；此前使用路由模拟 iframe 的测试未加载成功，已改用实际服务验证。该轮仅覆盖 HTTP 嵌入边界；下述后续验收覆盖完整实际审查组件，不与独立页面 GPU 验收混同。

内嵌实际组件 CPU 验收已完成：运行 `dag_1e516e8674e84f6d85a209bec43cfa52`，通过真实 iframe 页面预览、填写 `Codex embedded CPU smoke (not user approval)` 并提交，三个节点各一次 attempt、一个 committed 决定回执；最终发布成功，完整证据闭包通过，浏览器脚本错误为 0。收起后固定运行图读取成功。使用 Fake Backend，无新增 GPU 推理；不与真实单图质量结论混同。可移植复现脚本位于 `frontend/smoke/`，说明见使用指南。

收口验证：9 项 Python 编辑器测试、9 项 Vitest、7 项 Playwright、TypeScript/Vite、Ruff 和 mypy（85 个源码文件）通过。可移植内嵌 smoke 在已有历史记录的服务再次运行成功（`dag_b9e01fd7949d45aab472667fa49f8294`）；脚本以创建响应的精确 run ID 核验成功、三节点各一次 attempt 与单一决定回执，不以列表第一项替代本次结果。未重复声明全仓测试通过。

逐节点本地 profile 首片：AdapterRegistry 允许同一 Adapter key 按显式 Backend 名注册多个实现；绑定新增可选 backend 字段，旧无显式绑定计划的序列化保持不变。编辑器加载配置中的全部 profiles，`--profile` 为默认绑定；下拉目录按 Operator/Adapter 过滤，切换配置清除旧 profile_digest 覆盖，参数表单和契约面板读取对应 schema。

验证：53 项 Adapter/Engine/编辑器 Python 测试、8 项 Playwright、TypeScript/Vite、Ruff、mypy 通过。新增双实例执行回归证明各自调用对应实现，provenance adapter_identity 中固定 Backend 名，重启恢复不增加调用次数；缺少原 Backend 时拒绝恢复且不改写原节点证据。当前为 CPU 实现隔离验收，尚未进行同图两种真实模型组合运行。未声称完整 B2 完成。

双生成分支 CPU 验收：新增 `examples/dag-image-compare.yaml`，一次 proposals/人工选择后扇出到两个显式 profile 的生成节点。真实单图 Adapter 逻辑配合 Fake Backend 完成两个独立子发布；核对精确共享 SelectionInputBinding、独立 child run、各自 backend provenance、单一决定回执及恢复后节点证据不变。单图 Adapter 文件的 7 项测试通过；随后改为直接加载新示例的单项回归通过（未将两轮相加宣称 8 个独立测试）。两个真实模型的 GPU 组合尚未验收，示例中的 profile 名必须替换为本机已安装配置。

本地 profile 绑定及双生成分支接入后的全仓回归：`PYTHONPATH=src <MAIN_CHECKOUT>/.venv/bin/python -m pytest -q` 单次运行 **927 passed in 221.29s**。Ruff check、format check（170 文件）、mypy（85 源码文件）通过。此轮没有运行 GPU 推理；不能将该测试结果扩大为尚未验收的真实双模型组合或完整 B2 完成。同期修正设计文档的过期“当前代码事实”及指南单 profile 限制说明。

入口一致性修复：编辑器原先注册全部显式 profile，而 dag-image CLI 仅注册默认实现，导致画布导出的 backend YAML 在 CLI 编译失败。现共用 `image_adapter_registry()`；默认与显式绑定两种 YAML 的 CLI start/resume/decide 路径均通过。7 项 CLI/编辑器定向测试通过，mypy 86 源码文件通过；未新增 GPU 推理。

启动可观测性：`load_profiles(..., progress=...)` 增加阶段性耗时回调，CLI 服务将信息写到 stderr；身份内容和校验顺序不变，失败会标明阶段并清理上下文。新增 profile 测试验证回调不影响身份、失败回调及后续调用隔离；27 项 profile/editor 定向测试、Ruff、mypy 通过。

浏览器输入补齐：运行面板支持 PNG/JPEG/WebP 上传（20 MiB、25MP、单帧），保留服务器路径并显式选择来源。上传只持久化原始字节及图片身份，不创建运行；启动时提交精确 ArtifactRef，经端口及摘要验证后固定输入。失败替换会清除旧上传引用。独立审查发现超大声明尺寸触发 Pillow DecompressionBombError 时未转换 HTTP 错误，已修正并添加微型 PNG 回归。

验证：11 项 editor/execution Python 测试、9 项 Vitest、9 项 Playwright、TypeScript/Vite 构建通过；Ruff、mypy 通过。覆盖上传不派发、相同字节身份稳定、精确运行输入、缺失 Blob 拒绝、非法/截断/超大声明尺寸图片、HTTP Origin 与两种启动请求、前端上传失败替换。浏览器测试模拟上传响应，实际图片解码由 Python 测试覆盖；本轮未新增真实 GPU 推理或浏览器上传到真实模型的端到端验收。创建幂等键及画布多视图仍未完成。

运行创建幂等性：复用 Repository 的耐久 CreationReceipt，将客户端键绑定编译后的完整计划及精确图片 Artifact 身份。先预留 run ID，再写首份快照；重复 POST 返回现有运行而不重复 drain，参数或图片身份变化则冲突。已创建标记在任何执行派发前写入；索引丢失时拒绝重新创建。独立 review 找到“初始快照后、marker 前崩溃”和“忙时新键产生幽灵记录”边界，已修复并补测；直接从列表 resume/retry 也必须建立 marker。存储写入失败仍沿用 Repository poisoned 状态，不在不确定的写入状态下继续执行。

浏览器在请求前保存 sessionStorage 原请求和 UUID；响应未确认时可明确重试原请求，刷新或修改草稿不会改变它。明确放弃仅清除本地待办，不取消已有服务端运行。旧 API 无 key 调用仍兼容，但没有幂等保证。当前已完成运行的重放仍会重新编译并校验当前资源与图片身份；配置或路径内容变化时拒绝重放，可从运行列表查看已有结果。本轮未新增 GPU 推理。

本轮验证：67 项 editor/execution/workbench foundation/engine Python 回归单次通过（47.05 秒）；前端 9 项 Vitest、11 项 Playwright 及 TypeScript/Vite 构建通过，另单独重跑了改草稿后重试原请求的浏览器用例。Ruff、mypy（86 源码文件）及 Python 构建通过。测试包含预留后中断、快照后中断、运行中重放、服务重启、键冲突、索引损坏拒绝及直接恢复的 marker 修复；并未重复宣称全仓测试或真实 GPU 端到端已验证。

上传及创建幂等的实际 HTTP/浏览器 CPU 验收：可复现脚本 `frontend/smoke/embedded-review.cjs --upload-retry` 已接入真实服务。真实上传后运行列表为空；首次 POST 经真实服务处理后由测试拦截器丢弃响应；浏览器刷新并显式重试，原请求、图片引用与 run ID 均相同，列表只有一个运行。随后在实际 iframe 选择 mask、填写自动化检查人、确认发布；三个节点均 succeeded、各一次 attempt，决定回执一个，四类输出读取均 200 且非空，浏览器错误为 0。

外部证据：`<SMOKE_ROOT>/embedded-result.json`、`embedded-completed.png`；本次 run 为 `dag_c02d4669e4764242b703d04ae9b98ed7`。独立读取 Store 中该 BuildRun 后验证完整证据闭包通过，BuildRun Artifact 为 `sha256:581f9d187062a3f80156e05f37dd7ae31d14eb5b6843783d4ff6673c9b8fb6e4`。首次脚本读取浏览器响应体触发 Playwright inspector cache 错误，未创建运行；改用 route.fetch 转发真实请求并保留响应用于核对后通过。无新增模型/GPU 推理，不将本轮 CPU 流程验收等同于模型质量验收。

多视图画布执行首片：服务新增独立 `--multi-view-config`，严格加载具名 DA3/Open3D profiles，可与单图配置并存；通过专用 OperatorSpec 和多视图模板使用现有三个 Adapter。运行面板根据观测包输入显示 Artifact ID 字段；POST 使用 observations_ref，与 image_path/image_ref 互斥。编译和启动共用适用性检查，启动验证 ObservationBundle 内容及证据闭包后固定输入；复用原幂等请求与恢复路径，单图请求摘要形式保持兼容。

CPU 测试确认多视图编译就绪、启动/发布、重复请求、显式恢复、输出读取及输入来源拒绝，模型契约替身各调用一次；服务配置测试确认目录加载；浏览器测试确认观测包精确引用与刷新重试。后端 editor/execution 合跑 18 项通过，前端单测 9 项通过。首次浏览器新增用例漏处理模板覆盖确认框导致超时，修正脚本后该用例通过；未将其归为产品故障。Ruff、mypy 通过。当前尚未从真实画布运行 DA3/Open3D GPU，批量图片导入未实现；不得宣称完整 B2 完成。

最终前端回归单次 12 项 Playwright 全部通过；TypeScript/Vite 与 Python wheel/sdist 构建通过。本轮未获得独立 subagent review（现有 agent 线程配额已满），由主 agent 检查改动和验证结果。

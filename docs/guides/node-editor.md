# 节点编辑器

默认模式编辑和编译草稿；配置本地模型 profile 后，可从画布执行单图 DAG，并打开现有 mask 审查页面。当前支持节点选择服务配置内的本地 profile，执行入口仍要求一个 image 输入。

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

加载单图模板，在运行面板选择浏览器上传图片，或填写**服务所在机器的图像路径**，然后启动运行。上传和本地路径使用相同校验：支持 PNG、JPEG、WebP，最多 20 MiB、2500 万像素，拒绝动画及无法完整解码的文件；上传仅保存不可变 Artifact，不创建运行、不触发模型。启动时绑定上传返回的精确引用并重新校验证据。切换输入来源后只使用当前选中的来源。编译通过但不满足当前入口输入限制时，页面会显示不可运行原因；单图入口要求恰好一个名为 image 的 RGB 图像输入。启动时后端重新编译当前图，导入图像并固定不可变计划；之后编辑草稿不会改变该运行。profile 在服务启动时加载并核验本地资源身份，首次启动可能需要等待数十秒或更久，不会重新下载 PyTorch 或模型。

人工节点等待后，点击审查入口，在独立本机页面预览 mask、填写检查人并确认；确认后同一个 DAG 继续执行。画布页可以查看状态、错误、输出链接，并显式恢复或重试。关闭编辑器服务会等待当前工作线程结束；强制退出后的进程检查和恢复仍由既有 DAG 门控负责。刷新页面可从运行列表重新打开记录，读取状态不会自动恢复或重跑。

运行创建使用幂等键：浏览器先在当前标签页 sessionStorage 保存请求，再发送启动命令。若响应丢失或页面刷新，可点击“重试原创建请求”重发原图和输入；修改草稿不会改变待重试请求。同一键绑定同一计划和图片内容，返回原运行且不重复派发；参数、模型身份或服务器路径图片内容发生变化会拒绝复用旧键。重复返回的运行若尚未派发，可显式点击恢复/继续。明确放弃请求只清除浏览器待办，不取消可能已经创建的服务端运行；创建新运行前应先核对列表。恢复和重试使用当前 revision，失败时先重新读取状态。服务串行接收执行命令，人工决定执行期间不能启动另一条任务。

边界：模型 profile 属于服务配置，可按节点选择已安装且兼容的条目；输入支持浏览器上传与本地图像路径。基础参数表单从 Adapter schema 生成，支持字符串、数字、布尔值和枚举；单值枚举只读。数字失焦时应用，非法值显示“尚未应用”，保留先前草稿值；复杂对象与数组仍用 JSON 编辑。可移除显式覆盖以恢复默认值；多视图支持已有 ObservationBundle 引用（见下文）；HTTP Backend、ComfyUI 尚未接入画布。GLB 输出可通过链接读取，现有人工审查页可查看模型。

## 固定运行图

在运行面板选择历史或当前运行，点击“查看固定运行图”。弹出的只读图来自该运行已经持久化的计划，并显示轮询得到的节点状态；与正在编辑的草稿独立。无法验证计划或当前服务缺少对应 Adapter 时会显示错误，不用草稿替代。关闭运行图不会修改草稿。更新后端代码后需重启服务才能使用新的计划读取接口。

## 工作台内审查

人工节点等待时，先点击“准备人工审查”，再点击“在工作台审查 mask”。工作台以 iframe 加载现有本机审查组件，预览、检查人填写及确认仍通过原决定接口提交；也保留独立窗口链接。收起面板只移除嵌入视图，不取消计算或撤销决定，切换运行会清除旧审查视图。

### 内嵌审查 CPU 复现

以下脚本使用测试 Fake Backend 和真实 HTTP/审查页面，不加载 GPU；`<SMOKE_ROOT>` 必须是新的仓库外目录。

```bash
# 仓库根目录，先构建 frontend 静态资源
PYTHONPATH=src:tests <MAIN_CHECKOUT>/.venv/bin/python frontend/smoke/editor_server.py --root <SMOKE_ROOT>
# 另一个终端
node frontend/smoke/embedded-review.cjs <SMOKE_ROOT>/browser-config.json
```

脚本在实际 iframe 中选择 mask、填写明确的自动化测试检查人并确认，然后等待发布与固定运行图成功。浏览器结果和截图写到 `<SMOKE_ROOT>`；这不是用户批准，也不能作为真实模型质量证据。完成后 Ctrl+C 关闭测试服务。

## 逐节点本地 Backend 配置

服务读取配置文件中的全部已安装 profiles；`--profile` 仍指定旧图和未显式绑定节点的默认实现。选择节点后，在“Backend 配置”中选择兼容 profile，选择结果保存到 YAML 节点的 `backend` 字段。不同生成节点可选择不同配置；人工节点使用 Core，不接受模型 profile。切换配置会移除旧 profile_digest 覆盖，其余参数保留并由后端重新校验。固定计划包含显式 Backend 名称及该配置的参数/模型身份，资源或实现变化后不能当成旧绑定恢复。

本轮验证本地目录绑定、计划往返与多实例隔离；尚未验收两种真实模型在同一个画布运行中的组合。HTTP/ComfyUI 不包含在这个目录内。后端升级后需重启服务加载目录。

### 同一选区比较两个生成配置

加载 `examples/dag-image-compare.yaml`（也可启动时增加 `--template`）。把 `generate_first` 和 `generate_second` 的 Backend 分别改为目录内配置，再编译运行。SAM 只执行一次、选区只确认一次，两个生成节点读取同一个 SelectionInputBinding，分别生成发布与 provenance；当前调度器串行执行，不会同时抢占 GPU。示例 Backend 名为占位符，未替换前编译会拒绝执行。该图尚无模型质量评分或自动优胜选择。

编辑器导出的显式 `backend` YAML 也可通过 `dag-image start --pipeline <YAML>` 执行。CLI 与编辑器共用本地 profile 注册目录；两者需使用相同配置文件，`--profile` 只指定省略 backend 时的默认实现。已有服务仍持有运行目录锁时，不要用 CLI 同时修改该目录。

### Profile 启动进度

启用真实本地 profile 时，CLI 会在 stderr 输出 SAM checkpoint、SAM environment、每个 profile 的 source/model weights/environment 和最终一致性检查的开始、完成与耗时。模型快照摘要可能耗时较长；进度输出不跳过哈希，也不代表 GPU 已开始推理。服务地址只会在全部 profile 身份检查通过后打印。

### 上传与响应丢失验收

使用上面的 CPU smoke server，并使用新的空 `<SMOKE_ROOT>`，执行：

```bash
node frontend/smoke/embedded-review.cjs <SMOKE_ROOT>/browser-config.json --upload-retry
```

脚本通过浏览器上传真实图片；验证上传不创建运行后，将启动 POST 转发给真实服务，服务端创建成功后仅丢弃浏览器响应。刷新页面并重试原请求，核对请求内容、幂等键、ArtifactRef 和 run ID 不变，再完成 iframe mask 审查、发布和输出下载。脚本使用 Fake Backend，人工决定明确标注自动化测试，不是用户质量批准。

## 多视图运行入口

已导入同一 Store 的 ObservationBundle 可从画布启动。先按已有 `import-observations` 指南导入数据，保留返回的 Artifact ID；本入口不会把任意图片集合隐式转换成观测包。

多视图配置文件使用独立目录，例如（路径替换为已有本地资源）：

```json
{
  "default_profile": "da3-open3d",
  "profiles": {
    "da3-open3d": {
      "da3_python": "/existing/da3/env/bin/python",
      "da3_repo": "/existing/Depth-Anything-3",
      "da3_model": "/existing/DA3-BASE",
      "open3d_python": "/existing/open3d/env/bin/python"
    }
  }
}
```

```bash
PYTHONPATH=src <MAIN_CHECKOUT>/.venv/bin/python -m assets_generator.cli node-editor \
  --directory <RUN_ROOT>/editor --store <RUN_ROOT>/store \
  --multi-view-config <MULTI_VIEW_CONFIG>.json \
  --operators examples/dag-multi-view-operators.yaml \
  --template examples/dag-multi-view-asset.yaml --port 8767
```

加载模板后，在运行面板填写 ObservationBundle Artifact ID，点击启动。三个节点都可选择具名多视图配置；同一重建链必须满足既有 profile 和证据关系约束，不能任意混用不兼容来源。配置在服务启动时核验，使用已有环境，不下载权重。可同时增加单图的 `--config` 和 `--profile`，在同一服务使用两类模板。

编译与启动共用输入适用性检查：仅支持单个 `image` RGB 或 `observations` ObservationBundle 输入，均须接收标量 ArtifactRef。启动再次验证观测内容、证据闭包和绑定；幂等键、固定计划、输出读取及恢复与单图一致。可在运行面板直接选择 2–32 张 RGB PNG/JPEG/WebP 照片，每张最多 20 MiB；按浏览器选择顺序分配 view_000 等 ID，并显示文件列表。先逐张上传，再校验组包，只有点击启动才执行模型。拒绝重复 Artifact 和非 RGB 图片，不隐式转换，也不补相机、mask 或深度。失败时清除当前输入，可重新选择；之前成功上传的不可变图片可能留在 Store，重传相同字节会复用身份。仍可手填已有 ObservationBundle ID。真实画布 DA3/Open3D 的已有引用路径已完成 GPU smoke，批量上传接真实模型尚未重复验收。

真实服务启动后，可用 `frontend/smoke/multi-view.cjs <BROWSER_CONFIG>.json` 验收画布路径。配置包含 `url`（不含尾斜杠）、`observations`（Store 中现有观测包 Artifact ID）和 `root`（已存在的仓库外证据目录）。脚本通过 UI 启动，等待发布，核对精确输入与每节点一次 attempt，读取各输出并截图固定运行图。此命令会实际调用所配置模型，GPU 验收请串行执行。

## 模型预览

发布后点击“预览模型 · 节点名”，可在工作台旋转、缩放、平移模型或重置视角。面板显示固定 run ID，不跟随画布草稿变化；关闭或切换运行会释放渲染资源。读取仍经过服务端证据校验，不会提交决定或触发模型。保留 GLB 下载入口。

首版使用随应用打包的 Three.js，支持自包含 GLB、内嵌 bufferView 纹理和顶点颜色；拒绝外部/data URI，预览大小上限 128 MiB。无法创建 WebGL 或解析失败时显示错误。已验证真实发布顶点颜色模型，以及小型内嵌 PNG 纹理的画布像素；复杂材质和压缩扩展尚未覆盖。

已有发布结果可执行只读预览 smoke：`node frontend/smoke/preview.cjs <CONFIG>.json`。配置字段为 `url`、`run_id`、`node_id`、`root`；脚本打开预览、重置视角并关闭，验证没有变更请求、没有外部资源请求及 BuildRun 未变，并保存截图。不会启动模型。

切换节点 Backend 时，编辑器会清除旧、新 Adapter schema 中单值枚举的显式覆盖
（包括远程 endpoint、service_id、backend_digest），由新配置重新提供固定值。
seed 等可调参数保留；后端仍重新绑定并校验全部参数，切换不会改写已有运行。

## 图片节点输出预览

RGB/RGBA 节点完成后，运行面板提供“预览图片 · 节点 · 端口”。可同时展开多个
节点比较处理结果；透明区域显示棋盘背景，面板保留固定运行 ID。
仅在展开时读取输出，每次重新展开都通过后端证据校验，读取失败显示错误。
收起或切换运行会取消读取并释放图片资源；预览不会执行模型、恢复运行或提交决定，
原始输出链接仍保留。

数组和对象参数现在可在参数表单中按字段编辑 JSON，点击“应用字段”后写入草稿。
无效 JSON 或顶层类型不符不会应用；其他字段及同类算子的其他实例保持原值。
“移除覆盖”恢复 Adapter 默认值。嵌套类型、必填字段及语义仍以后端编译为准；
应用字段本身不会启动运行。

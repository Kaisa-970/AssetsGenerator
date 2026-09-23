# 节点编辑器

已有符合统一协议的图生 Mesh 服务时，可在左侧“添加模型服务”填写地址，检测后确认添加，无需编辑 Registry JSON 或重启编辑器。操作与部署示例见[添加自己的图生 Mesh 模型](add-shape-model-service.md)。首版支持 RGBA → Mesh；其他能力仍使用下方原有配置入口。


默认模式编辑和编译草稿；启用执行配置后，可按节点选择已注册的本地或 HTTP Backend。当前运行入口支持单个 image（RGB/RGBA）或 observations（ObservationBundle）输入，且至少有一个处理节点。ComfyUI 图片链已有实验接入，真实部署验收仍未完成。

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

## 先体验一条不依赖模型的管线

先按上节构建前端，再在仓库根目录启动：

```bash
PYTHONPATH=src <MAIN_CHECKOUT>/.venv/bin/python -m assets_generator.cli node-editor \
  --directory /tmp/assets-cpu-editor/editor --store /tmp/assets-cpu-editor/store --port 8770
```

1. 打开打印的地址，点击 cpu-image-editor 模板并确认替换当前画布；自动编译后进入运行页。
2. 默认使用浏览器上传，选择 RGB 图片，确认输入缩略图。此时没有运行模型。
3. 点击“启动新运行”。界面会自动准备输入、检查执行范围并创建运行；检查期间按钮显示“正在检查当前配置…”。
4. 运行成功后展开“预览图片 · encode · image”。复用历史结果或运行到子图时，界面会先显示执行摘要并要求确认。
5. 刷新页面后，从运行列表选回该记录。查看状态不执行任务；显式恢复成功运行也不会重做已完成节点。

上传/路径导入与预检分开：准备输入保存不可变引用，预检本身只读；确认时用这些引用再次核验。
若配置、输入或复用证据改变，创建会停止，要求重新检查，不会悄悄重跑原计划复用的模型。
目前预检覆盖 Core 契约和复用证据，不检查远端可达性、GPU 资源或全部 Adapter 私有语义；
使用来源运行时仍要求其完整快照证据可读。缺失证据不会由预检修复。

这个示例只将 RGB 图片显式编码为 PNG，不生成 mask 或 3D。目录中的其他算子
可能只有契约，没有注册实现；不能据此认为所有模块都能运行。手工搭图、两个
实例扇出与服务重启的可重复步骤见 [CPU 模块指南](add-dag-module.md)。

## 操作

- 从左侧选择模板，或点击/拖放算子到画布。添加管线输入并编辑其端口契约。
- 从输出端口拖到输入端口；同一算子可放多个实例，实例 ID、参数和绑定分别保存。
- 选择节点，在右侧编辑实例名称、参数 JSON、adapter/backend。默认目录只包含 OperatorSpec；尚无安装的模型 registry 时，填写绑定仅记录请求。
- 图或参数修改后自动编译；“编译校验”保留为诊断入口。后端重新编译并要求显式汇合关系；静态通过不等于 Backend 已验证或可运行。应用注入 AdapterRegistry 时，会校验注册适配器参数和绑定，启用执行时同时校验注册的适配器和参数。
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

边界：模型 profile 属于服务配置，可按节点选择已安装且兼容的条目；输入支持浏览器上传与本地图像路径。基础参数表单从 Adapter schema 生成，支持字符串、数字、布尔值和枚举；单值枚举只读。数字失焦时应用，非法值显示“尚未应用”，保留先前草稿值；复杂对象与数组仍用 JSON 编辑。可移除显式覆盖以恢复默认值；多视图支持已有 ObservationBundle 引用（见下文）；HTTP Backend 已接入，配置见 [远程服务指南](remote-shape-service.md)；实验性图片链见 [ComfyUI 指南](comfy-image-profile.md)。二者均由受信服务配置注册，不能在画布任意填写地址替代注册。GLB 输出可在工作台内预览或下载。

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

本轮验证本地目录绑定、计划往返与多实例隔离；尚未验收两种真实模型在同一个画布运行中的组合。本节配置文件只负责本地模型；HTTP/ComfyUI 使用各自的启动配置参数注册到编辑器目录。后端升级后需重启服务加载目录。

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

编译与启动共用输入适用性检查：专用表单支持单个 `image` RGB/RGBA 或 `observations` ObservationBundle 输入；多个命名输入可通过标量 ArtifactRef 映射绑定（见下文多输入章节）。启动再次验证观测内容、证据闭包和绑定；幂等键、固定计划、输出读取及恢复与单图一致。可在运行面板直接选择 2–32 张 RGB PNG/JPEG/WebP 照片，每张最多 20 MiB；按浏览器选择顺序分配 view_000 等 ID，并显示文件列表。先逐张上传，再校验组包，只有点击启动才执行模型。拒绝重复 Artifact 和非 RGB 图片，不隐式转换，也不补相机、mask 或深度。失败时清除当前输入，可重新选择；之前成功上传的不可变图片可能留在 Store，重传相同字节会复用身份。仍可手填已有 ObservationBundle ID。真实画布 DA3/Open3D 的已有引用与批量上传路径均已完成 GPU smoke；批量上传后的服务重启恢复见[验收报告](../reports/multiview-browser-upload-real.md)。

真实服务启动后，可用 `frontend/smoke/multi-view.cjs <BROWSER_CONFIG>.json` 验收画布路径。配置包含 `url`（不含尾斜杠）、`root`（已存在的仓库外证据目录），以及二选一的 `observations`（Store 中现有观测包 Artifact ID）或 `images`（2–32 张本地 RGB 图片的绝对路径数组）。提供 `images` 时，脚本实际操作浏览器多图上传并记录组包请求。脚本通过 UI 启动，等待发布，核对精确输入与每节点一次 attempt，读取各输出并截图固定运行图。此命令会实际调用所配置模型，GPU 验收请串行执行。

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
仅在展开时读取输出，每次重新展开都通过后端证据校验；读取失败可点击“重新读取图片”，只重读结果，不重跑节点。
收起或切换运行会取消读取并释放图片资源；预览不会执行模型、恢复运行或提交决定，
原始输出链接仍保留。

数组和对象参数现在可在参数表单中按字段编辑 JSON，点击“应用字段”后写入草稿。
无效 JSON 或顶层类型不符不会应用；其他字段及同类算子的其他实例保持原值。
“移除覆盖”恢复 Adapter 默认值。嵌套类型、必填字段及语义仍以后端编译为准；
应用字段本身不会启动运行。

选中算子节点后点击“复制节点配置”，会创建新的实例 ID，并复制已应用参数、
Adapter、Backend 和上游输入连接，便于设置多分支实验。原节点及其下游连接不变。
尚未应用的表单文字不属于节点配置，不会复制；新节点仍需编译，并由用户显式
创建新运行后才执行。

编译失败时，“编译结果”面板列出后端诊断及端口信息。诊断能对应当前图中节点时，
点击“定位节点”会居中显示该节点并打开配置；不会修改连线、参数或执行运行。
全局错误或不在当前图中的定位仅显示说明，不猜测应修改的节点。完整编译 JSON
继续保留，便于核对计划及错误详情。

节点目录标记“已注册实现”或“仅契约 · 未配置实现”，可勾选“只看已注册实现”
并结合算子名称搜索。本地默认 Adapter 和显式 Backend 注册都计入目录。
注册不证明模型已验收，也不保证当前参数、输入关系或运行资源满足执行条件；
最终仍需后端编译绑定。仅契约节点可以保留在草稿中。

## 区分草稿、计划与执行证据

- 草稿是当前正在编辑的配置。数组/对象字段需点击“应用字段”；未应用文字不会进入保存、编译或运行，可逐字段放弃。修改后旧编译结果失效。保存期间继续编辑时，提示会说明只保存了请求时的快照。
- 固定运行图来自所选运行的计划。点击节点可查看当时绑定的 Operator、Adapter、Backend、参数和摘要；它不随当前草稿改变。
- 节点“执行记录”中的“输入输出证据”展示该次 attempt 的实际输入、输出、provenance 引用和摘要，不会读取 Blob 或派发计算。
- “已登记的证据问题”列出恢复检查记录的 Artifact ID 与原因，可能包含历史问题；当前是否阻塞以节点与运行状态为准。未定位到节点的证据阻塞会单独显示。查看记录不会补回缺失文件。

加载草稿、导入 YAML 或从历史运行载入配置期间，如果画布又有编辑，迟到响应会
被拒绝以保留当前内容；重新发起加载即可。以上只读查看不等于质量批准，成功状态
也不替代模型效果或资产质量评测。

## 多输入运行绑定（API）

运行入口原先支持单个 `image_ref` 或 `observations_ref`。对于拥有多个 Pipeline
输入的图，后端现在支持按端口名提交完整的不可变 ArtifactRef 映射：

```json
{
  "pipeline": {"pipeline": "...", "version": "1", "inputs": {"image": {}, "mask": {}}, "nodes": {}},
  "input_refs": {
    "image": {"artifact_id": "sha256:..."},
    "mask": {"artifact_id": "sha256:..."}
  },
  "idempotency_key": "..."
}
```

映射必须与 Pipeline 输入集合完全一致，每个值只能是 `{artifact_id}`；Core 会按
OperatorSpec 校验 kind、schema、carrier、digest 和证据完整性，再固定到
`named_actual_inputs`。缺失、额外或损坏引用会拒绝创建；重复幂等请求必须使用
同一映射，不会重跑已有节点。

运行面板已支持多个命名的标量 Artifact 输入：RGB、RGBA 和 binary mask 可上传，
其他类型可填写已导入 Artifact ID。集合或 StructuredValue 输入尚无对应表单，
编译反馈会说明当前入口不适用。单图和 ObservationBundle 保留专用表单。

## RGB 与二值 mask 组合

编辑器现在提供 `apply_binary_mask@1` CPU 节点。它接收两个命名输入：`rgb_image`
和 `binary_mask`，输出 `rgba_image`。在多输入管线中，运行面板会为图片和 mask
分别显示上传控件；mask 上传只接受单帧灰度 PNG，像素必须是 0/255 且至少有一个
前景像素。也可以填入已经导入的 Artifact ID。

`examples/remote-mask-shape.yaml` 展示了完整连接：

`RGB + binary mask → RGBA → remote shape → canonicalize → QA → publish`

远程 shape 仍必须通过启动配置注入可信 profile；画布只保存节点绑定和参数，不保存
模型路径或未校验的服务地址。

无需模型即可验证双输入画布：先构建前端，再启动 CPU 示例服务。

```bash
npm --prefix frontend run build
PYTHONPATH=src <PYTHON> examples/cpu_image_editor.py \
  --directory <DATASET_ROOT>/mask-editor --port 8770
```

打开页面，加载 `cpu-image-mask`，点击编译校验，在运行页分别上传同尺寸 RGB 图片
和二值灰度 PNG mask。启动后点击 `预览图片 · composite · rgba`。
可重复浏览器验收使用真实服务，无模拟 API：

```bash
node frontend/smoke/image-mask.cjs http://127.0.0.1:8770 \
  <RGB_IMAGE> <MASK_PNG> <FRESH_EVIDENCE_DIRECTORY>
```

验收 mask 须同时包含前景和背景；证据目录需预先创建，不能覆盖旧验收文件。
脚本检查真实编译、双上传、完整绑定、浏览器 alpha 预览和显式恢复不增加 attempt。
这是 CPU 合成验收，不包含 Shape 模型推理或服务重启。

多输入管线还可复用当前选中历史运行的可查看输出：输出列表会为 kind 匹配的端口
显示“用作输入”。点击后服务器验证运行归属、成功状态和完整证据闭包，再返回精确
ArtifactRef；前端核对 schema 后绑定。绑定本身不创建新运行，仍需明确点击启动。
损坏证据不能通过此入口修复；目标输入的 frame/unit 与关系仍由启动时后端校验。
当前按钮范围为可查看的标量 Artifact 输出和多输入表单，不含 StructuredValue 或集合。

历史输出复用的真实浏览器验收（同一 CPU 示例服务、新建 Store 与证据目录）：

```bash
node frontend/smoke/reuse-output.cjs http://127.0.0.1:8770 \
  <RGB_IMAGE> <SAME_SIZE_MASK_PNG> <FRESH_EVIDENCE_DIRECTORY>
```

脚本先在浏览器执行编码，再加载 mask 模板并点击“用作输入”，上传 mask 后显式
创建第二个运行。检查精确引用一致、绑定不产生额外运行、原运行未改变及 RGBA 可预览。

单图 `image` 入口也支持历史输出复用。在“图片来源”选择“使用历史输出”，从运行
列表选择已有运行，再点击匹配 RGB/RGBA 输出的“用作输入 image”。服务器验证输出
证据后显示精确 Artifact ID；仍需点击启动才创建新运行。输入契约变化时绑定清空。
这允许把 mask 合成产生的 RGBA 交给单图 Shape 管线，无需下载再上传。

## 分割满意后继续提取

在运行列表选中已有分割结果，在 `mask` 输出旁点击“继续提取 · 节点名”。系统读取当前展示的
不可变运行快照，保留分割实际使用的原图（包括前面缩放等处理），核验分割及其必要上游能否复用。
通过后会列出将复用的节点，并说明新运行只执行提取；点击“确认继续提取”才创建新运行。
启动时再次核验，证据或绑定变化会停止创建，不能把复用悄悄改成重新分割。

旧运行保持不变，新运行可通过“查看固定运行图”查看追加的提取节点。此入口不覆盖当前草稿，
不继承人工决定。当前仅支持具有可核实联合 `mask` 的 `text_segmentation@1`；不支持的遮罩
会显示拒绝原因。来源快照的完整证据仍须可读，且当前注册实现须能核对原计划。

## 失败后的操作提示

节点错误旁会显示后端依据已保存状态给出的原因及处理引导。“请求核验并重试”表示可以提交
一次显式命令，不表示已允许重新推理；后端仍会检查输入、证据、旧进程及原远程作业。
提示缺失、revision 不匹配或编辑器正在执行其他命令时，按钮不可用。页面不会为了生成提示
查询远程作业或恢复运行，也没有强制重提入口。

## 比较两次结果并选用

1. 从运行列表打开第一份结果，点击对应输出的“加入比较 A”。
2. 打开另一份运行，点击“加入比较 B”，再打开“比较两次结果”。
3. 图片可并列查看，模型分别打开预览；每项显示来源运行、节点、端口和固定快照。系统不评判优劣。
4. 只有点击某项的“用作下游输入”才改变当前草稿输入；之后预览另一项不会更换绑定。
5. 关闭比较，检查执行范围并确认新运行。原运行保持不变。

选用按钮仅针对当前草稿已有的匹配标量输入，不会自动修改图或插入类型转换。当前单输入专用
启动表单仍限 image/observations；GLB 等其他 kind 可用于已有多输入图的匹配端口，不能据此
认为所有单输入入口已开放。候选 mask 的选择同时保存原图、候选来源和固定快照绑定；错配原图会被拒绝。
比较项存于当前页面，刷新后需重新加入；固定读取仍要求完整来源快照证据可核实。

## 跨运行确认沿用人工选择

新运行停在人工节点时，可在该节点的“复用人工决定”区域选择来源运行，
点击“检查旧选择是否适用”。来源必须是同一节点实例，且旧节点成功、
当前节点等待人工；输入引用、候选证据、Operator 契约及 Adapter/Backend
绑定必须完全相同。仅同尺寸或视觉相似不够，不匹配时重新人工选择。

核验成功后查看原选择内容及选择人，填写本次确认人，再点击
“确认沿用旧选择并继续”。系统记录属于当前运行的新决定、确认时间、
旧决定及固定来源快照引用；旧运行保持不变。确认人是自报身份，不是认证或质量批准。
这复用的是选择内容，人工节点仍按当前运行执行，不自动采用旧节点的输出。

发送前保存当前标签页的幂等请求。响应不明确时可“重试原确认请求”；刷新后
先读取服务端状态，仍在等待时可重发原请求。若决定已经保存但节点执行中断，
按中断节点的显式重试流程继续，不能用重发确认偷偷重跑模型。

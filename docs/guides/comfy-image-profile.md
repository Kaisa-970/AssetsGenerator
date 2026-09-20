# ComfyUI 图像 profile 实验入口

此入口运行受信的 ComfyUI API workflow，产出图片和边界证据。尚未完成真实 ComfyUI
验收；已接入实验性 DAG/画布图片节点，不是 3D 资产发布。Core 不安装或加载 ComfyUI 模型。
服务须由用户预先部署，profile 只能由受信配置提供，勿接受浏览器任意上传的配置。

## 配置

本地 JSON profile 必须包含全部字段：

```json
{
  "schema": "comfy-image-profile@1",
  "service_id": "comfy-image-local",
  "endpoint": "http://127.0.0.1:8188",
  "prompt": {
    "1": {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}},
    "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "AssetsGenerator"}}
  },
  "parameter_schema": {"type": "object", "properties": {}},
  "defaults": {},
  "parameter_targets": {},
  "image_targets": {"image": ["1", "image"]},
  "output": {"node": "2", "index": 0, "mode": "RGB"},
  "deployment_claims": {"revision": "unverified", "models": "unverified"}
}
```

这是用于说明 API 图格式的图片复制示例，未运行真实 ComfyUI 验收。实际 prompt
应来自目标服务的 API workflow 导出。普通参数通过 parameter_schema/defaults 与
parameter_targets 映射到 `[node, input]`，图片只通过 image_targets 绑定。
整个 profile 的规范化内容决定 Backend digest；修改配置需使用新的作业数据库。

请求文件引用同一 Artifact Store 内已存在的 RGB/RGBA PNG Artifact，例如：

```json
{
  "images": {"image": {"artifact_id": "sha256:<实际的64位Artifact摘要>"}},
  "parameters": {}
}
```

## 命令

```bash
assets-generator comfy-image validate --profile /path/to/profile.json
assets-generator comfy-image preflight --profile /path/to/profile.json
assets-generator comfy-image start --profile /path/to/profile.json \
  --directory /path/to/jobs --store /path/to/store --key image-001 \
  --request /path/to/request.json
assets-generator comfy-image status --profile /path/to/profile.json \
  --directory /path/to/jobs --key image-001
assets-generator comfy-image resume --profile /path/to/profile.json \
  --directory /path/to/jobs --store /path/to/store --key image-001
```

validate 仅校验本地配置，不证明服务或模型可用。start 领取任务后上传并提交一次，
只查询一次结果，不持续轮询。未完成、历史缺失或传输不确定返回退出码 3；之后
使用 resume 查询原任务，不要重复 start 或更换 key 来绕过占用。status 只读取记录。

成功结果在作业数据库中提供 `image` 和 `evidence` 输出，可分别下载：

```bash
assets-generator comfy-image download --profile /path/to/profile.json \
  --directory /path/to/jobs --key image-001 --output-id image --output /path/to/image.png
assets-generator comfy-image download --profile /path/to/profile.json \
  --directory /path/to/jobs --key image-001 --output-id evidence --output /path/to/evidence.json
```

下载验证已发布 Blob 摘要与长度，原子写入新文件，拒绝覆盖已有路径。它不恢复或
派发计算，也不是包含所有依赖的可移植 Store 备份。证据同时固定在 Artifact Store，
只覆盖复合边界，内部模型、自定义节点身份仍标为 unverified。保留 jobs 与 Store。
上传后、提交绑定前中断会保守阻塞，尚不支持自动续跑或远程取消。匹配原请求的上游失败
历史会持久化并记为 COMFY_EXECUTION_FAILED，可离线恢复；缺失或不匹配的历史仍
保持不确定，不应手工删除日志来解锁。

## DAG 服务端实验命令

同一 profile 可用于单 RGB 图片输入/输出的服务端。监听器只接收作业，执行需另一个
终端显式调用，尚无后台自动调度。DAG 客户端 Adapter 已接入，编辑器配置见下文；
协议 fixture 的通过不代表真实 ComfyUI 部署已验收。

```bash
assets-generator comfy-service serve --profile /path/to/profile.json \
  --directory /path/to/jobs --port 8771
assets-generator comfy-service list --profile /path/to/profile.json --directory /path/to/jobs
assets-generator comfy-service execute-next --profile /path/to/profile.json \
  --directory /path/to/jobs --store /path/to/store
assets-generator comfy-service recover --profile /path/to/profile.json \
  --directory /path/to/jobs --store /path/to/store --key <原作业key>
```

serve 只绑定本机地址，不代表提供公网认证。execute-next 只领取一项，未知结果保持
running 并阻塞后续领取；recover 不重新提交推理。不要将此前本地图像 CLI 请求混入
DAG 服务队列，二者请求格式不同，建议使用独立 jobs 目录。

## 编辑器启动配置

新增 --comfy-config，文件中 endpoint 是本项目 comfy-service 网关地址，profile 文件
中的 endpoint 则是上游 ComfyUI 地址，二者不要混淆。相对 profile 路径按配置文件
所在目录解析。例如：

```json
{
  "default_profile": "comfy_first",
  "profiles": {
    "comfy_first": {"endpoint": "http://127.0.0.1:8771", "profile": "profile.json"},
    "comfy_second": {"endpoint": "http://127.0.0.1:8771", "profile": "profile.json"}
  }
}
```

```bash
assets-generator node-editor --directory /path/to/editor --store /path/to/core-store \
  --comfy-config /path/to/comfy-backends.json \
  --template pipelines/comfy_image_chain_v1.yaml --port 8767
```

节点目录提供 image_transform@1 / comfy_image@1，实例通过 Backend 选择配置。
模板先通过显式 encode_png@1 节点，将 RGB raster_image 编码为独立 PNG Artifact；
PNG/JPEG 解码像素保持不变，RGBA、灰度或调色板输入不会隐式转换。
已完成注入上游协议的 CPU 浏览器运行验证，尚未进行真实 ComfyUI 验收。网关监听
和显式执行命令仍需单独运行，不会因编辑器轮询自动派发远程模型。

## 退出码

执行动作（start/resume/execute-next/recover）确认任务失败时返回 1，并在 JSON
error 中保留错误码和详情；结果未知返回 3。命令参数或校验错误返回 2。成功执行、
空队列及成功的只读查询返回 0；status/list 的退出码只表示查询是否成功，任务
状态以 JSON 为准。


### 可复现的 CPU 浏览器 smoke

从仓库根目录运行（Python 环境须包含测试依赖，frontend 已安装依赖并构建）：

```bash
PYTHONPATH=src:tests python frontend/smoke/comfy_server.py --root /tmp/comfy-browser-smoke
```

保持该终端开启；另一个终端依次执行：

```bash
node frontend/smoke/comfy-run.cjs /tmp/comfy-browser-smoke/browser-config.json start
# 服务终端输入 execute，等待打印 succeeded
node frontend/smoke/comfy-run.cjs /tmp/comfy-browser-smoke/browser-config.json middle
# 服务终端再次输入 execute，等待打印 succeeded
node frontend/smoke/comfy-run.cjs /tmp/comfy-browser-smoke/browser-config.json finish
# 可再次执行 finish，检查完成后恢复；最后服务终端输入 quit
```

使用空的新目录。脚本通过浏览器创建运行、恢复运行，并检查各节点仅一次 attempt、
输出可下载及无页面异常。Core 与网关为真实 HTTP；ComfyUI 上游、上传和图像结果为
注入 fixture，不执行模型，不代表真实 ComfyUI 兼容性或生成质量验收。

完成上述 finish 后，可执行只读图片预览验证：

```bash
node frontend/smoke/comfy-run.cjs /tmp/comfy-browser-smoke/browser-config.json preview
# 仅在此隔离测试服务终端输入 damage-output，破坏测试生成的最终图片
node frontend/smoke/comfy-run.cjs /tmp/comfy-browser-smoke/browser-config.json damaged
```

preview 验证图片实际解码并保存截图；damaged 验证后端拒绝损坏输出且页面显示
读取失败。两者断言没有变更请求、BuildRun 前后完全一致，不调用恢复或模型。
damage-output 只适用于这个脚本创建的临时 fixture，不用于实际资产目录。


### 只读部署预检

validate 只验证本地配置；preflight 对 profile 指定的上游服务逐类请求
GET /object_info/<class_type>，检查 workflow 所需节点类存在，并检查所选输出
节点声明 output_node=true。成功返回 0，缺类、无输出声明或查询失败返回 1，
报告包含具体 issues、观察声明摘要及 profile 身份。

该命令不创建作业数据库、不上传图片、不提交 prompt。报告始终标记
deployment_verified=false：节点声明不能证明模型文件可用、输入语义兼容、
自定义节点身份可靠或推理成功。它是配置诊断，不能替代真实 workflow 验收，
也不参与已有运行恢复或自动授权重试。

### 随仓库提供的图片复制配置

可直接使用 examples/comfy-copy-profile.json 与 examples/comfy-copy-editor.json，
无需从文档复制 JSON。两者配合 pipelines/comfy_image_chain_v1.yaml：
显式 PNG 编码 → 图片复制 → 图片复制。两个 Backend 名称指向同一服务，
用于先核对上传、执行、下载和连接关系，不加载生成模型，也不产生新的 3D 资产。

先按目标部署调整 profile 的上游 endpoint；编辑器 JSON 的 endpoint 是外层网关。
可先离线校验：

```bash
assets-generator comfy-image validate --profile examples/comfy-copy-profile.json
```

目标 ComfyUI 服务已启动时，再执行 preflight、comfy-service serve 和 node-editor。
网关作业仍需显式 execute-next，完成后在画布点击恢复以导入结果、推进下一节点。
服务不存在时 preflight 失败不代表可以重新提交已有作业。该样例已通过本地配置与
DAG 绑定测试，尚未在真实 ComfyUI 部署执行，不作为真实验收报告。

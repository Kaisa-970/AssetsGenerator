# ComfyUI 图像 profile 实验入口

此入口运行受信的 ComfyUI API workflow，产出图片和边界证据。尚未完成真实 ComfyUI
验收，也未接入 DAG/画布；不是 3D 资产发布。Core 不安装或加载 ComfyUI 模型。
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
终端显式调用，尚无后台自动调度；DAG 客户端 Adapter 仍待接入。

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
该接线有启动/目录测试，尚未完成浏览器交互验收或真实 ComfyUI 验收。网关监听
和显式执行命令仍需单独运行，不会因编辑器轮询自动派发远程模型。

## 退出码

执行动作（start/resume/execute-next/recover）确认任务失败时返回 1，并在 JSON
error 中保留错误码和详情；结果未知返回 3。命令参数或校验错误返回 2。成功执行、
空队列及成功的只读查询返回 0；status/list 的退出码只表示查询是否成功，任务
状态以 JSON 为准。

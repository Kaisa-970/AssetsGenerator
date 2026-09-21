# SAM3D 服务接入与统一协议边界

状态：部署服务已升级至 REST 1.1，增加耐久幂等、按键查询、能力声明和结果证据。
独立 HTTP 客户端及耐久兼容执行器已有实现；尚未注册 DAG 节点，也未完成真实推理。
开发入口见 [兼容服务指南](../guides/sam3d-bridge.md)。
以下对“当前 API 未声明”的描述是升级前分析；部署变化见 [部署记录](../reports/sam3d-protocol-deployment.md)。
目标服务为用户提供的 SAM3D REST API v1，页面使用 Gradio 4.44.1。
以 `/api/guide/download` 和 `/api/openapi.json` 为当前接入依据，不通过网页内部回调绕过鉴权。

## 分层与首版选择

保持 React Flow → OperatorSpec/AdapterRegistry → 统一远程作业协议 → 模型服务。
可控服务应直接实现统一协议；不能修改的第三方服务由独立兼容服务转换协议。
模型特有代码属于服务接入层，DAG engine 不新增按模型名称分支。

首版采用 image + binary mask → SAM3D → GLB。暂不接提示点、分割 UI 或 Gaussian PLY。
SAM3D 的 image/mask 保留独立精确引用，空间与尺寸关系由显式 relation validator 检查。
计划新增 `masked_shape_generation@1`，不修改现有 RGBA `shape_generation@1` 的端口。
其输出复用 mesh、PBRMaterial、BackendNativeFrame；下游复用 canonicalize/QA/assemble/export。
这属于待实现契约，当前 OperatorSpec 尚未添加。

## 统一协议复用与服务能力

| 项目 | 现有统一协议 | SAM3D 当前 API | 接入要求 |
| --- | --- | --- | --- |
| 提交 | POST /v1/jobs，耐久 key/digest | multipart POST /api/v1/jobs | 包装层转换 image/mask/options |
| 查原请求 | GET /v1/jobs/by-key/key | 未声明 | 推荐服务端支持请求键与摘要冲突检查 |
| 查任务 | GET /v1/jobs/id | GET /api/v1/jobs/id | 保留任务 ID，不更换任务 |
| 状态 | queued/running/succeeded/failed | queued/waiting_gpu/running/completed/failed | waiting_gpu 作为运行中的排队状态；completed 先导入验证再发布 |
| 结果 | 声明 blob 摘要与长度 | 下载 GLB/参数/mask，未声明摘要 | 下载后固定字节摘要，不冒充服务端证明 |
| 身份 | service_id/backend_digest | 文档未声明模型摘要 | 服务端补代码/权重/环境身份，或明确受信部署声明的验证边界 |
| 空间 | BackendNativeFrame | 文档未说明 | 核实导出坐标与单位，不能从 GLB 扩展名猜测 metric scale |

统一协议解决传输与生命周期；OperatorSpec 继续作为本项目唯一端口契约权威。
服务能力声明只能用于兼容性核对，不自动改写 OperatorSpec。

## 不可跳过的提交窗口

1. 外层统一服务先耐久记录原请求与归属，再 claim 任务。
2. 在发送 SAM3D POST 前，将上游提交授权固定到外层任务记录。
3. 收到任务 ID 后耐久固定，恢复只查询这个 ID。
4. POST 响应丢失、5xx、非协议响应、落盘中断：保留 running/unknown 并阻塞重提。
5. 当前 API 没有按请求键查询时，无法自动找回丢失的 ID；外层幂等不能消除这个窗口。

不能只加一份可丢失的旁路 JSON 就允许恢复补写。外层任务必须持有不可替换的授权归属，
恢复发现内部记录缺失时拒绝重建。实现时参考已有远程/ComfyUI 故障窗口测试，但不能
借用 ComfyUI 字段假装 SAM3D 任务是 ComfyUI prompt，也不重写 DAG 状态机。

推荐服务端增加：调用方 submission_key + request_digest；同键同摘要返回原任务，
同键异摘要 409；按键查询；重启保留映射。不要仅在内存字典去重。
若暂时不能改服务端，兼容层必须明确标注未知提交无法自动恢复。

## 已实现客户端

`sam3d_http.Sam3DClient` 提供 health、submit_once、query、download。
这是传输组件，不是生产执行器；调用方必须在 submit_once 前耐久记录授权。
没有自动 POST 重试、轮询、重定向、取消、删除或服务发现。

- Key 由调用者从环境变量/私有配置读取，不进入请求身份、Artifact、画布或日志。
- 图片须为无方向变换需求的 RGB PNG/JPEG；mask 为同尺寸、非空二值灰度 PNG。
  有 EXIF 旋转时要求先通过显式转换处理，不能发送后发生未记录的旋转。
- 默认与范围遵循服务公开 options；未知参数拒绝，不把 TRELLIS pipeline_type 传入 SAM3D。
- 下载仅构造固定同源路径；不信任服务返回的任意 URL，不转发 Key 到其他主机。
- 当前下载仅返回有界 bytes，未声称 GLB/材质/空间已验证；PLY 不能通过网格入口下载。

## 后续验收顺序

1. HTTP 客户端真实本机监听测试：multipart、响应丢失不重发、鉴权脱敏、路径与输入限制。
2. 确认服务可否修改及模型/空间声明后，实现外层授权、上游 ID、查询恢复和结果固定。
3. 输出实际 mask 与输入比较、参数回读、GLB 自包含/材质/坐标检查，保存精确证据。
4. 接 OperatorSpec、AdapterRegistry、模板及中文表单，验证图片 + mask → SAM3D → 发布。
5. 使用私有 Key 执行一次真实模型验收，再做服务/网络中断场景。

当前已完成客户端、服务端升级及外层兼容服务首版。授权、回执和终态证据与外层作业同库保存；
结果摘要、实际 mask、参数、部署和空间声明通过校验后，输出字节与成功状态原子发布。
Key 不进入身份或证据；模型语义前向未验证，空间明确为相对尺度。
DAG 节点、中文表单及真实推理验收尚未完成。

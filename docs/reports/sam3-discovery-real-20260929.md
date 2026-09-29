# SAM3 服务能力发现真实验收

日期：2026-09-29

本次将已有 SAM3 文字分割服务包装层升级为可发现服务，未修改 SAM3 模型仓库、权重或推理 runner。旧的 `8773`、`8774`、`8775` 监听和 runtime 保留不动；新版本使用独立 `runtime-discovery-v2` 和端口 `8776`。

## 验收事实

- 远端主机：`ypk@172.16.89.51`
- 新服务监听：`127.0.0.1:8776`
- 本地 SSH 隧道：`127.0.0.1:18776 -> 172.16.89.51:127.0.0.1:8776`
- `GET /v1/service-descriptor` 返回 HTTP 200。
- descriptor 声明：`text_segmentation@2`、`sam3_text_jobs@1`，默认 `confidence=0.5`。
- 新 Backend digest：`sha256:819b7a38510fd4a4c303b6ecf6402f7c958a981f8cbb4e73772f8d478303dc77e`。
- worker 已以 `runtime-discovery-v2` 启动。
- 本地 Core 通过真实 HTTP 完成检测、安装和目录摘要恢复；选中的 capability、operator 和 endpoint 均正确保存。

本地验证命令使用 `detect_service()`、`DraftEditor.add_model_service()` 和独立临时 Store，结果为：

```text
operator = text_segmentation@2
capability_id = text_segmentation@2
adapter = discovered_text_segmentation@2
execution_kind = remote
```

## 身份与兼容边界

发现代码、服务包装层和 HTTP descriptor 入口已纳入 SAM3 deployment digest。旧 runtime 因持久化身份不匹配不会被新代码复用，这是预期的安全保护。新服务保留原有文字分割 job wire：提示词仍由 text Artifact 转成旧 `text_segmentation@1` 请求字段，未向旧 handler 强行加入 `capability_id`。

该验收证明服务发现、绑定和 worker 启动，不证明本轮重新执行了 GPU 分割质量，也不证明文字分割到 3D 发布的完整真实链路。真实 GPU 任务应在确认服务稳定后单独串行验收。

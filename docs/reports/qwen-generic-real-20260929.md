# Qwen Image 2.1 通用服务接入验收（2026-09-29）

## 当前结论

两项能力的真实 DAG 执行、下游缩放、Core 重开恢复和服务端完成后重启均通过。服务端旁路包装既有 stable-diffusion.cpp，
Core 使用 GenericRemoteCapabilityAdapter，没有 Qwen 专用 Adapter。
本轮发现动态多输入声明未传递关系约束，因此补充 descriptor `relations` 到
OperatorSpec 的通用支持；不能称为“现有 Core 零改动即支持两种能力”。

## 已核实的边界

- 原生模型支持 RGBA。早期服务误声明 RGB，Core 拒绝导入，未伪造成功。
- 可靠包装层第一次真实调用完成了原生 4-step 推理，但拒绝非不透明 RGBA，
  留下 running／未知状态。该旧任务及旧数据目录保留，没有重发或修改成成功。
- 最终包装约定显式白底合成为 RGB，转换实现进入 Backend 身份。
- 原生 API 无提交幂等键，包装层提交后的传输异常仍须人工核实，不自动再次推理。
- 推理进程及磁盘部署清单均固定；重启原生进程需要重新生成身份和数据目录。

## 自动验证

动态契约、服务目录、通用 CPU HTTP 链、内容校验、关系校验相关 pytest：77 项通过。
服务替身 unittest：14 项通过，包含真实 Core HTTP 客户端、中文摘要、并发与重启阻塞、透明度合成。改动文件 Ruff check／format 通过。

单文件 mypy 未通过：既有 RemoteShapeAdapter 可选依赖 fallback 出现 no-redef；
本轮不声称全量 mypy 或全仓测试通过。

运行记录、输入与生成图片保存在仓库外 `<DATASET_ROOT>/qwen-generic-20260929-final/`。
低步数与单个案例只证明协议、数据流和恢复边界，不替代模型质量 benchmark。

## 真实结果

最终服务 Backend：`sha256:3d172ec6d7d225dc9ebd5cac811530620138462546eb42d3434699eb6e9a0d93`。
服务 URL `http://172.16.88.217:18082`；两个 capability 分别安装并持久化独立 Backend。

| 流程 | run | 结果 |
| --- | --- | --- |
| 文字 → Qwen 文生图 → Resize | `dag_34b440dd38024f638f78a1c83efcb426` | 两节点各一个 attempt，RGB 512×512 → 256×256 |
| 上轮图片＋文字 → Qwen 图生图 → Resize | `dag_edc71a44a2c04da29469e4fbe52b62f1` | 两节点各一个 attempt，RGB 512×512 → 256×256 |

均使用 seed 42、20 steps、512×512。输出 Artifact 闭包校验成功。
每次收取均重开 Core；完成后显式 recover，`completed.json` 与 `restored.json`
的 node_states 完全一致。图生图引用文生图的精确输出 Artifact，没有重新上传替代图。

服务端 bridge 以相同 manifest／Store 重启：PID 1677280 → 1677831，原生推理
进程未重启。前后两条成功记录、请求摘要及结果完全一致，按键查询返回原结果；
没有新增任务。证据 `verification/bridge-restart.json`。不证明推理中断可自动恢复。

视觉观察：文生图得到红色茶壶；图生图保留形状和构图，但“改成蓝色”仅部分生效，
仍有明显红色。协议与管线接入通过，不能据此宣布编辑质量达标。

工作台已启动，两条草稿分别为 `text_to_image` 和 `image_to_image`。
浏览器走查范围为页面打开、草稿加载、真实服务能力及结果读取；真实推理由
Python DAG 验收脚本触发，不冒充用户在浏览器完成整条链。

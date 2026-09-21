# SAM3D REST 协议升级记录

日期：2026-09-21。用户授权通过 SSH 修改其部署服务。远端目录无 Git；本轮未提交本地改动。

## 已部署

REST 1.1 保留原网页和旧 API，新增可选 submission_key/request_digest/backend_digest：
服务核对原始上传字节和展开后的规范参数，同键同内容返回原任务，同键冲突返回 409。
请求先耐久登记再入队。新增 by-key 查询和 capabilities 鉴权接口。

登记数据库位于 outputs/protocol，结果位于 outputs/api；登记不跟随 7 天结果清理。
结果缺失返回 410 和原 receipt，不重新生成；登记库缺失/身份不匹配拒绝重建。
完成任务新增文件 SHA-256、大小、evidence.json；终态元数据禁止后续修改。
旧任务不补造这些字段。失败区分 SERVICE_RESTARTED 与 INFERENCE_FAILED。

能力声明依据实际导出代码记录右手系 Y-up、relative unit，语义前向未核验、
无 metric-scale 证明。启动时计算源代码、checkpoint、辅助模型缓存内容摘要；
Python/关键包仅版本身份，明确不是完整环境构建身份。运行期间修改资源须重启。

## 部署与验证

远端目录：`<SAM3D_DEPLOY_ROOT>`，用户提供的 sam3d-docker 部署位置。
原文件备份在 `backups/protocol-20260921/`，旧镜像标签为
`sam3d-web:before-protocol-20260921`；新增代码为 app/api_protocol.py、app/api_identity.py，
更新 api_server.py 及两份 API.md。隔离测试保存在 protocol-staging/test_protocol.py。

在原容器中使用 web/model 替身进行隔离测试，8 项通过：幂等、冲突、重开、过期、
提交登记后队列故障、登记库丢失/替换、终态不可变、队列满时仍能重放原请求。
首次运行误用容器 /app 的旧 api_server，3 失败；指定 staging 工作目录后通过。
这些是 CPU 协议测试，不代表模型验收。

构建复用原 Docker 层与现有运行环境，未下载模型或 PyTorch。上线前 API 无活动任务、
Gradio queue_size=0、GPU 利用率 0；随后重建容器。网页健康、鉴权 health/capabilities
读取通过，protocol_version=1.1、active_api_jobs=0、sam3d_loaded=false。
验证使用容器环境里的 Key，没有输出或复制 Key 到仓库。

## 未完成

尚无真实模型生成、真实网络响应丢失重试、重启跨任务恢复或烘焙输出回读验收。
服务端声明和协议已落地，不等于 AssetsGenerator 已注册可运行 SAM3D 节点。
下一步是统一外层作业协议兼容层、双输入 Operator、结果导入与坐标/材质校验。

保留原镜像和备份便于回退；回退前必须确认没有活动任务。回退不删除 outputs/protocol，
旧版本不支持新的幂等保障，不能继续让 Pipeline 按 1.1 能力调用。

## Reviewer 修正与部署可审查性

已逐字节核对远端 app 文件、本次暂存文件，并用运行容器内 SHA-256 核对
api_server.py/api_protocol.py/api_identity.py/API.md，四份一致。
[部署补丁](patches/sam3d-rest-1.1.patch) 以原备份为基线，包含新增模块；
[摘要清单](patches/sam3d-rest-1.1-manifest.json) 记录原文件、部署文件及运行镜像身份。
这保存了服务代码改动的可审查依据，不代表该无 Git 部署目录已有独立版本库。
补丁仅改 app 文件；部署时根目录 API.md 同步复制 app/API.md。未收录 Key、模型或结果。

客户端区分 Sam3DConflict（提交 409）、Sam3DExpired（经过校验的 410 receipt），
lookup 返回 not_found/registered/expired_or_missing。404 仅对未固定 job 的按键查询
表示本次未找到，不授权重提；已固定 job 后查询不到原键仍阻塞。410 至多读取 64 KiB，
验证错误码、原请求键/摘要、Backend 摘要、任务 ID 与路径，只暴露白名单 receipt 字段。
没有原绑定或格式/身份不符的 410 仍是 unknown，原始错误正文不进入异常消息。

HTTP 测试新增丢失 POST 响应后按原键恢复，不发生第二次 POST，并校验原请求/Backend。
这是客户端到本机 HTTP 测试服务的协议验收，不是生产服务中断或 GPU 验收。

最终本轮检查：SAM3D/既有 HTTP/远程协议合跑 **71 passed（20.69 秒）**，
Ruff lint/format、mypy（136 文件）、git diff --check 通过。包含 410 超大正文和
畸形正文脱敏回归；未运行全仓测试、未重新部署、未调用真实推理。

## 统一协议兼容层首版

在上述客户端修正基础上，新增 Sam3DBridge、Sam3DServiceStore 和独立模块命令入口。
启动监听不推理；首次执行先固定授权再发送唯一一次 POST；恢复仅查询原键和原任务。
上游授权、回执和完成证据与外层作业保存在同一 SQLite 库，禁止从丢失的归属重新授权。
兼容代码摘要和上游部署一起进入外层 Backend 身份，身份变更拒绝使用原库。

完成时先固定服务端摘要，再校验文件字节、实际 mask、实际参数、请求/部署和空间声明；
GLB 要求自包含、有限非空几何。保留原 GLB 字节及顶点颜色，单位只映射为 relative_unit，
不再次旋转模型。四份输出与成功状态原子提交；已成功输出或终态证据缺失时拒绝恢复。
格式转换用的 staging 不作为权威证据库，外部响应不暴露其中的 Artifact 引用。

本轮最终 SAM3D bridge/client + remote HTTP/protocol 合跑 **86 passed（22.29 秒）**。
包含授权后发送前中断、响应丢失重开恢复、过期阻塞、错误语义拒绝、事务回滚、
成功证据丢失拒绝、CLI 初始化及缺库拒绝；桥接执行器使用上游替身，客户端测试使用本机 HTTP。
修改文件 Ruff lint/format、mypy（140 文件）、git diff --check 和模块 --help 通过。
未运行全仓测试或构建；没有新增真实模型推理，也未声称线上网络中断验收通过。

开发用法见 [指南](../guides/sam3d-bridge.md)。仍待双输入 OperatorSpec/AdapterRegistry、
画布表单及真实 SAM3D 生成验收；此次兼容层不修改 DAG engine 或现有 shape_generation 端口。

### 提交前队列阻塞修正

领取任务、输入验证及授权现在在同一 SQLite 事务中完成。领取后授权前中断会完整回滚；
非法 payload、mask 或参数记录 SAM3D_INVALID_INPUT 终态，不产生上游提交，也不阻塞下一条。
明确 HTTP 拒绝记录 SAM3D_REJECTED，与拒绝证据原子提交；409 冲突仍保持未知并阻塞。
恢复本地失败检查原请求摘要与终态证据，不要求不存在的上游 receipt，也不会重新推理。
保留“授权事务已提交、POST 尚未发送”窗口的保守阻塞策略。

修正后相关测试合跑 **91 passed（23.00 秒）**，修改文件 Ruff lint/format、
mypy（140 文件）通过；新增非法任务后继续执行、领取/授权事务中断回滚、明确拒绝后继续、
409 冲突保持阻塞回归。未新增真实推理验收。

### 后续双输入节点开发（提交 28daea1 之后）

已新增双输入 OperatorSpec、远程适配器、启动配置注册和最小 YAML 示例。
DAG 的 input_blobs/input_digest/binding_digest 封装在服务边界核对；不更改 DAG engine。
本步 SAM3D bridge + remote profiles + 既有 remote shape 测试 **35 passed（10.87 秒）**，
Ruff、mypy（141 文件）、git diff --check 通过。尚未进行完整 HTTP DAG、画布或 GPU 验收，
也没有将最小输出图表述为完整发布流程。本步改动保留未提交以便继续验收。

### 完整资产链 CPU HTTP 验收

`pipelines/sam3d_masked_shape_v1.yaml` 现包含生成、canonicalize、geometry QA、
masked_shape_asset_assembly、asset_export。新增 assembly 契约显式消费 sam3d_evidence
与 actual_mask；必须有同 run/node/attempt 的生成 provenance，不能替换成其他证据。
Release 沿已有 assembly provenance 引用两份证据，保留原单图 RGBA assembly 契约。

可复现命令：`PYTHONPATH=src python -m pytest -q tests/test_sam3d_http_release.py`。
测试启动两个真实回环 HTTP listener：外层统一作业服务和 SAM3D REST CPU 替身；
使用真实客户端 multipart、按键查询、文件下载、SQLite 重开及 DAG repository 重开。
四种组合覆盖正常/丢失 POST 响应，以及顶点颜色/内嵌纹理。每次上游仅收到一次 POST，
所有节点仅一次 attempt；完成后恢复状态一致。检查 Release 中 assembly provenance 的
证据引用、错配证据拒绝、GLB 外观保留、原始网格字节不变，以及不对称网格经记录的
canonical 矩阵和单次导出坐标变换后的顶点一致。重开的是控制器/数据库；不声称验证了
真实 SAM3D 容器重启或 GPU 推理中断，也不声称这是 SIGKILL 测试。

最终相关链路与 HTTP 协议合跑 **136 passed（40.52 秒）**，mypy（141 文件）、
修改文件 Ruff lint/format、git diff --check 通过。未运行全仓测试或 build。
浏览器操作与真实 SAM3D GPU 验收仍待进行；本轮未提交。

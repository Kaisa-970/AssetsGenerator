# SAM3D 兼容服务首版

当前供开发验证：已实现统一协议监听、一次提交、按原键恢复和结果导入。
双输入节点已可通过启动配置注册，完整资产发布链已通过 CPU 替身 HTTP 验证。
尚未完成浏览器操作和真实 GPU 生成验收。

在 node-workbench 工作树运行 `PYTHONPATH=src python -m assets_generator.sam3d_service_cli --help`。
Python 使用项目已有虚拟环境。每条命令都需要：

- `--deployment`：经核对的 REST 1.1 capabilities 响应中的 **deployment 对象**保存的 JSON；不要保存 Key。
- `--endpoint`：SAM3D 地址，如 `http://172.16.89.51:7861`。
- `--directory`：仓库外专用运行目录；初始化前必须不存在。

动作按顺序使用：

1. `identity` 查看统一服务身份。摘要包含上游部署及兼容代码，改动后不得复用旧身份。
2. `init` 创建一次 SQLite 持久库。
3. `serve --port 8772` 启动仅绑定回环地址的统一协议监听，**不会自动推理**。
4. 统一协议调用方上传 RGB 图和同尺寸非空二值 mask 字节，再提交作业。
5. 另一终端执行 `execute-next`，明确授权一次上游提交；没有自动循环或 POST 重试。
6. `list` 查看任务；`recover --key 原请求键` 查询同一任务并导入结果。

只有 execute-next / recover 要求私有环境变量 `SAM3D_API_KEY`，可用 `--key-env` 指定名称。
不能把密钥写入 deployment、Pipeline、命令参数或 Git。

请求 payload 包含 operation=`masked_shape_generation@1`、image_digest、mask_digest、
parameters（SAM3D 参数对象）和 backend_digest（上游 deployment 的摘要）。
外层请求的 Backend 摘要则使用 identity 动作输出的兼容服务摘要，两者不能混用。
这是服务请求格式；节点端口由已注册的 masked_shape_generation@1 OperatorSpec 定义。

恢复只查询已登记键。授权之后、POST 之前崩溃也会保守阻塞，不把“查不到”解释成允许重发。
成功结果的字节与成功状态一起事务提交；之后缺失或损坏不会通过重新下载掩盖。
目录下 staging 仅用于格式转换，权威输出是 SQLite 中的 mesh、shape_metadata、
sam3d_evidence、actual_mask 四份字节。shape importer 仅接收其中前两份；DAG 适配器会
单独持久化后两份证据，经 assembly provenance 关联到 Release。服务输出保留 GLB 自带外观，不再次旋转 Y-up 网格。

这里只验证可自包含读取的 GLB 和相对尺度声明，不证明材质视觉质量或真实尺寸。

## 双输入 DAG 接入进度

新增 `masked_shape_generation@1` 与 `remote_masked_shape@1`，配置示例为
`examples/sam3d-remote-config.json.example`，完整发布图为 `pipelines/sam3d_masked_shape_v1.yaml`。
示例中的两种摘要必须替换为经核对的实际值。节点目录由启动配置注册，保持旧远程单图配置兼容。
模板连接生成 → canonicalize → QA → masked assembly → export。
assembly 显式消费服务证据和实际 mask，并核对它们来自同一次生成；Release 包含 assembly provenance。

图片和 mask 可分别绑定输入；运行前检查尺寸关系，服务边界检查精确 Artifact 身份和上传摘要。
导入核对服务证据、请求参数、Backend、网格及实际 mask，防止两条输入或结果错配。
已完成 CPU 替身完整 HTTP DAG 验证，尚未做浏览器或 GPU 验收。

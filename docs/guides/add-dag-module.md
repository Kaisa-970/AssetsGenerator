# 接入一个可组合 DAG 模块

本文面向实现 Adapter 的开发者。画布用户配置的是已注册实现，不能上传 Python
代码或任意服务地址来安装模型。Core 不加载模型/CUDA；已有模块可直接复用，
新模型应采用独立进程或受信 HTTP 服务。

## 先确定模块边界

同一个 Operator 表示同一种输入输出语义，例如 image_transform@1 接收 RGB PNG，
输出 RGB PNG 和复合边界证据。不同模型实现同一契约时注册多个 Backend，
不为每个模型复制一套端口定义。图片、mask、点云和网格不是可互换的类型；
跨类型转换必须显式定义 Operator。

端口只定义在 OperatorSpec。选择已有 kind、schema、载体与 cardinality；
空间输出必须声明 frame/unit。两个输入若必须共享观测、相机或坐标系，应在
OperatorSpec 中引用具名 relation validator，不能只凭 kind 相同就允许汇合。
仅确实独立的输入使用 independent_inputs@1。

## CPU 模块的最小路径

先阅读并运行 [CPU 菱形示例](generic-dag-core.md)：完整代码在
examples/dag_diamond_demo.py，端口在 examples/dag-operators.yaml，
接线在 examples/dag-diamond.yaml。

实现类提供 spec: AdapterSpec 和 execute(context) → NodeExecutionResult。
context.inputs 来自已校验端口；context.parameters 是规范化、固定的配置。
新增内容通过 context.store.persist_bytes 或 persist_structured 产生 Artifact，
不要改写输入 Blob。原样透传可以返回同一 ArtifactRef；转换必须产生对应的新身份。
返回字典键必须匹配 Operator 输出端口。

注册步骤：

1. load_operator_specs 读取唯一端口契约；compile_pipeline 使用
   require_explicit_joins=True。
2. registry.register(adapter) 注册默认候选，或
   registry.register_backend("backend-name", adapter) 注册显式实例绑定。
3. registry.bind_plan(static_plan) 固定实现身份、参数 schema/defaults 与配置。
4. 用 DagRepository 和 DagEngine 创建/执行运行；不要在调度器中按 node_id 分支。

AdapterSpec 声明参数而不重复端口。支持对象、数组、标量、enum、数值上下限和
数组 minItems/maxItems；未声明关键字拒绝。默认值也校验。模型路径、环境、
权重和代码身份须由受信 profile 固定，不能藏在无身份的实例变量里。
当前实现摘要覆盖 Adapter 类所在模块，不能据此声称所有传递依赖均被固定。

## 模型进程、HTTP 与 ComfyUI

| 路径 | 实现参照 | 必须保持的边界 |
| --- | --- | --- |
| 本地独立环境 | src/assets_generator/dag_image_adapters.py | 使用 context.worker 注入的受控 Worker；父子运行按 child_context 登记，不自行 subprocess 启动绕过门控 |
| HTTP 服务 | src/assets_generator/dag_remote_adapter.py、dag_remote_shape.py | prepare_payload 无网络副作用；声明 input_blobs；import_result 校验实际编码、身份及语义，不把 HTTP 200 视为成功资产 |
| ComfyUI 复合图 | [profile 指南](comfy-image-profile.md) | 固定 API workflow 和输入输出映射；内部证据未核实必须标记 unverified；未知提交只查询原作业 |

远程 Adapter 须继承 RemoteNodeAdapter，execution_kind="remote"；
remote_endpoint、service_id、backend_digest 必须是固定单值 enum 和默认值。
远程作业由 Core 的持久化提交路径调度，不在普通 execute 中偷偷联网。
真实模型可用性需要独立验收，CPU fixture 仅验证协议与契约。

## 接入画布

DraftEditor 的目录从 OperatorSpec 和 AdapterRegistry 生成，不另写前端节点模板。
当前 CLI 的 profile 加载器支持已实现的模型族；加入全新 Adapter 类需要在受信
Python 启动代码中注册，不支持任意动态插件发现。

执行模式下 DraftEditor 直接使用 NodeEditorExecution 的 OperatorSpec 和关系注册表；
额外传入的 Operator 文件若与执行服务不一致，启动时拒绝。自定义启动代码仍应让
NodeEditorExecution 与 DagEngine 使用一致的关系注册表。静态编译支持的图不一定满足当前运行输入入口；
编辑器当前支持单图 image 或 observations 入口，开发者不能以目录可见替代执行验收。
新增关系应先登记到执行服务使用的注册表，画布编译随后复用该注册表。

## 验收顺序

先跑一个节点的输入/输出契约，再跑两个同类实例，检查参数、Backend 和 provenance
实例身份独立。再验证扇出/汇合与关系拒绝；最后执行失败、恢复、输入输出损坏测试。
进程或 HTTP 模块需覆盖响应丢失/执行器退出，证明不会重复启动未知状态的模型；
成功证据缺失时不得重新生成以掩盖损坏。

真实 GPU smoke 串行执行，复用已有环境和权重。报告分别记录协议、真实运行和质量，
不要把它们合并成一个“通过”。修改实现或 schema 后，旧固定计划可能拒绝恢复；
应保留历史并创建新计划，而不是修改原运行记录绕过身份校验。

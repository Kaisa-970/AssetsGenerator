# ComfyUI 复合 Backend 首版边界

状态：接入设计，尚未实现 Adapter 或真实服务验收。目标是将可配置的 ComfyUI
workflow 作为单个复合节点连接到 Core DAG，复用 Artifact、作业身份和发布校验，
不要求用户把内部每个 ComfyUI 节点重新编写成 Core Operator。

## 上游核实

2026-09-21 查阅固定 revision
[`0f74f7fb9f83a78bf46188fd4fd53e6bc44c1ae8`](https://github.com/Comfy-Org/ComfyUI/tree/0f74f7fb9f83a78bf46188fd4fd53e6bc44c1ae8)：

- [server.py](https://github.com/Comfy-Org/ComfyUI/blob/0f74f7fb9f83a78bf46188fd4fd53e6bc44c1ae8/server.py)
  的 POST /prompt 接受可选客户端 UUID prompt_id，经验证后调用 prompt_queue.put。
- [execution.py](https://github.com/Comfy-Org/ComfyUI/blob/0f74f7fb9f83a78bf46188fd4fd53e6bc44c1ae8/execution.py)
  的 PromptQueue.put 直接 heapq.heappush，没有按 prompt_id 检查重复请求。
- 服务提供 /queue、/history/{prompt_id} 查询，但查询不到不能证明从未执行；
  内存历史清理或进程重启都可能丢失观察证据。

以上仅证明该固定 revision 的代码行为，不保证其他部署版本。首版必须核验所接
版本，不能将 client_id、固定 prompt_id 或 HTTP 200 当作耐久幂等承诺。

## 最小实现路线

以现有 RemoteServiceStore 作外层耐久作业边界，新增专用 ComfyUI 服务 handler
及内部提交记录，不让浏览器任意指定地址、节点代码或服务器文件路径。
首个复合节点处理一种明确 Operator 契约；先验证图像输入输出边界，再扩展到
拥有明确 native frame/material 契约的 shape workflow。不能把任意图片输出
包装成 shape_generation。OperatorSpec 继续唯一声明端口。

受信启动 profile 包括 API workflow JSON、允许覆盖的节点输入及参数 schema、
输出节点与输出槽位映射、部署身份清单。实际参数替换后保存 submitted workflow
规范化摘要；同时保留模板摘要和映射版本。不能仅摘要编辑器 workflow 布局 JSON。
实际提交的 API 图须验证 node ID、class_type、引用边和映射目标；有歧义的输出拒绝。

## 不确定提交与恢复

外层 submission_key 仍有耐久唯一性。内部在 POST 前写 prepared 记录，固定
prompt UUID、实际 workflow、输入 Artifact/上传内容摘要和服务身份；prepared
持久化失败时不得联网。标记 sending 后才允许首次 POST，禁止 HTTP 自动重试。

若响应丢失或进程在 sending 后退出：只查询原 prompt UUID 和可验证的请求关联，
不自动 POST。匹配的 queued/running 继续等待；完整成功历史进入输出校验；
未找到、关联冲突或上游重启后历史丢失保持 unknown 并阻塞重试。固定 UUID
没有改变这一规则。不要调用全局 interrupt 来取消不属于当前作业的工作。

收到成功结果后，先耐久记录精确输出引用和摘要，再导入本地 Artifact。
ComfyUI 历史、输出文件后续丢失时，已经固定且校验通过的本地结果可离线恢复；
没有固定的结果不能从“同名文件”重建。不能复用本地 ProcessWorker 退出证据
假装远程 ComfyUI GPU 进程已退出；需独立的远程观察/不确定状态模型。

## 输出与来源

下载只允许 profile 指定服务内的显式接口，禁止任意 URL、重定向及路径逃逸。
校验尺寸、编码、kind、schema 和数据关系后导入。声明为 shape 的结果还必须
提供 native frame、unit、材质和几何校验；输出节点成功不等于资产可发布。

provenance 记录实际 workflow 摘要、部署 ComfyUI revision、custom node 身份、
模型/权重摘要、实际参数、prompt UUID、外层 job identity、输入输出 Artifact 映射。
缺少内部证据的项明确标为 unverified，标明 provenance 覆盖仅到复合节点边界；
不得因此拒绝所有可用复合节点，也不得伪造内部完整链或自动启用跨运行缓存。

## 验收顺序

1. CPU 协议服务验证准备日志、响应丢失、固定 UUID 重复入队风险、历史清理、
   服务重启未知状态、输出下载与内容身份；断言不确定时没有第二次 POST。
2. 一个小型真实 ComfyUI workflow 验证 API 图参数替换、上传下载、来源清单和
   Core Artifact 边界。复用已有环境；当前未发现现成部署，不自动安装模型。
3. 将复合节点接入 registry/画布，用同一 Operator 的本地与服务节点比较；
   最后验证真实 shape workflow 和发布。没有真实验收前保持实验性状态。

## 首个实现：单次提交日志

已新增独立 SQLite ComfySubmissionJournal。它将外层键、部署声明、实际 API 图和
预分配 UUID 固定，并在调用传输前耐久标为 sending。不同连接不能重复领取；
响应丢失、KeyboardInterrupt 或错误 prompt_id 后重开仍禁止再次 POST。
acknowledged 只代表收到对应提交回执，不代表 workflow 成功。

5 项 CPU 单元回归通过，Ruff/mypy 通过。当前未接 HTTP、队列/历史查询、输出
导入或 DAG；调用者还须保证部署内容已验证、HTTP 无自动重试，并将日志归属
固定在外层作业。数据库丢失后的外层阻塞尚需接入，不能自行重建日志恢复提交。

### HTTP 提交与原作业查询

新增 ComfyClient，将 POST /prompt 接入单次提交日志；先核对日志固定 endpoint，
提交不自动重试、不跟随重定向，响应限长且严格解析 JSON。history 仅 GET 原 UUID，
返回未经信任的历史对象；空对象不修改 sending，也不授权重发。

使用本机真实 HTTP 服务覆盖正常响应、服务收包后断连接、307、超大响应及重复
JSON key，重开 SQLite 后均不产生第二次 POST。日志与 HTTP 合跑 10 项通过，
Ruff/mypy 通过。历史内容关联、结果固定、上传下载及 DAG 仍未接入；没有运行
真实 ComfyUI，不能声明复合节点可用。

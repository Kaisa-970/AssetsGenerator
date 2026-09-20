# 节点编辑器 B2 状态核对

更新：2026-09-21。此文核对当前实现与设计第 15.5 节，不宣称项目或完整 B2 已完成。

| 要求 | 当前证据 | 尚需工作 |
| --- | --- | --- |
| 从画布创建固定计划与运行 | node_editor 编译/绑定；单图真实 smoke；多视图真实 DA3/Open3D smoke | 更多组合的验收 |
| 人工等待与确认 | 实际 iframe CPU smoke；真实单图 mask 确认；耐久决定回执回归 | 更多人工 Operator |
| 重启、显式恢复与重试 | 单图服务重启记录；DAG 中断报告；多视图完成后服务重启恢复 | 多视图推理中服务崩溃仍未验收 |
| 结果预览 | 固定运行图；经过证据验证的 asset/release/qa/GLB 输出链接 | 画布内 GLB 预览已实现并通过真实顶点颜色模型 smoke；内嵌 PNG 纹理像素检查、WebGL 不可用及关闭/切换回归已补齐；复杂材质/压缩扩展尚未覆盖 |
| 输入体验 | 浏览器单图上传、多图组包；精确引用绑定；创建请求幂等重试 | 双图上传 → DA3/Open3D → 发布及完成后服务重启已独立验收；更多输入组合待验收 |
| 保留既有入口 | 固定 workbench、单图 CLI、多视图 demo 均保留 | 未授权替换或删除旧入口 |
| 模型可配置 | 逐节点本地/远程可信 profile；SAM-only 配置；双 HTTP 服务独立发布 CPU 回归；真实远程 TRELLIS.2 脚本 smoke | 同图 TRELLIS.2/TripoSR 独立发布已通过 API 和浏览器验收；真实 ComfyUI 尚未验收 |

相关证据见 [B2 实现记录](node-editor-b2.md)、[真实单图画布验收](node-editor-b2-real-smoke.md)、[多视图迁移](generic-dag-multi-view.md)。真实运行数据位于各报告声明的仓库外目录，不提交模型、Store 或生成资产。

实际结果预览及其交互回归已经补齐。远程 HTTP 作业服务、模型进程门控、可信启动目录、
编辑器远程状态/恢复入口和逐节点服务选择已接入。真实 TRELLIS.2 脚本链已验收，
SAM-only 配置完成真实环境启动核验，双服务模板完成 CPU 发布与离线恢复。
当前实现范围与限制见 [远程 Backend 状态表](../design/remote-backend-v1.md)。
SAM-only 配置的[真实浏览器完整链](sam-only-browser-real-complete.md)及
[真实执行器中断门控](trellis-executor-interruption.md)已分别验收；
后者不覆盖 HTTP 服务重启；另已完成[真实父 DAG 故障重试到发布](parent-dag-retry-real.md)，
该项为 API 脚本验收，不是浏览器重试或采样阶段中断验收。
实验性 ComfyUI 图片链现已通过 CPU 浏览器运行、恢复及图片输出预览验证，
另有只读部署声明预检；仍未完成真实 ComfyUI 模型验收。
不把已完成输出恢复或一次 HTTP 200 当作推理中断验收。

近期 CPU 编辑器已验证从空画布手工连线、双实例扇出、成功运行服务重启恢复，
以及固定绑定/实际输入输出证据的只读查看；共享输出 Blob 丢失会阻塞两个引用节点，
不会重新编码补回文件。操作与可重复脚本见 [模块接入指南](../guides/add-dag-module.md)。
这些检查不增加真实模型或推理中断的验收范围。

多图上传、真实重建与完成后服务重启恢复已通过[独立验收](multiview-browser-upload-real.md)。

同一 RGBA 的 TRELLIS.2/TripoSR 双分支独立发布已通过[真实 API 验收](dual-shape-real.md)。

双真实模型的画布配置、RGBA 上传、发布下载和重复恢复也已通过[浏览器验收](dual-shape-browser-real.md)。

真实 TRELLIS.2 执行期间的 HTTP 监听服务 SIGKILL、重连及原作业发布
已通过[独立 API 验收](http-listener-crash-real.md)，不等于模型或主机宕机验收。

接下来的真实验收按以下顺序推进，沿用隔离目录与串行 GPU：

1. 多视图推理中断的独立验收。
2. 具备真实 ComfyUI 部署后，验证 profile 预检、提交与图片输出；仍明确内部身份边界。

上述是待执行验收，不是已完成事实。不能通过降低身份校验或修改旧运行来复用不匹配的证据。

以下回归记录保留各次代码基线和当时结果，不代表之后提交已重复通过完整套件。

## 本次回归

在多图上传提交 `1abadca` 后启动完整 Python 回归；测试过程中只有前端格式整理及文档变化，没有修改 Python 实现。单次结果 **961 passed in 249.86s**。格式整理后的前端 Playwright **12 passed**，TypeScript/Vite 构建通过；Ruff lint/format（174 文件）、mypy（87 源码文件）、Prettier 指定三文件检查通过。此轮未执行 GPU，不能替代上表未完成的验收。

## 远程协议基础加入后的全量回归

在 `9e0d018`（严格远程 HTTP JSON 解析）上执行单次完整 Python 测试：**1026 passed in 235.85s**。测试期间只修改设计与状态文档，未修改 Python 实现。仓库级 Ruff format（184 文件）、Ruff lint、mypy（92 源文件）、sdist/wheel 构建及 git diff --check 通过。此次没有重跑前端浏览器测试或真实 GPU 推理；远程调度与真实服务仍未验收。

## 耐久远程服务联调后的全量回归

在 `f2a19fd` 上运行单次完整 Python 回归：**1051 passed in 256.14s**。运行期间仅编辑文档，没有修改 Python 实现或测试。Ruff lint/format（194 文件）、mypy（97 源文件）、sdist/wheel 构建与 git diff --check 通过。未重跑浏览器测试或真实 GPU 推理。远程 CPU 菱形、服务重开和 worker SIGKILL 不重放已有测试，真实模型进程门控与自动恢复仍待实现。

## 选区远程链与后处理来源补齐后的回归

代码基线 `70b9ee0`。单次完整 Python 回归 **1104 passed in 281.70s**；
Ruff lint/format（220 文件）、mypy（110 源文件）及 sdist/wheel 构建通过。
前端 Vitest **15 passed**、Playwright **14 passed**，TypeScript/Vite 构建通过。
Vite 仍提示 GLB 预览 chunk 超过 500 kB；此次没有改变打包策略。
测试期间未修改实现，构建没有产生受版本控制的差异。

新增单图 workflow 回归验证 `postprocess_mode` 进入 shape provenance；
这不追溯修改既有发布证据，也不证明真实运行使用了某一种后处理路径。
本轮未重跑 GPU，没有新增纹理质量或模型能力结论。

## SAM-only、观测关联与双服务模板后的全量回归

代码基线 `05037ba`，测试期间未修改实现。单次完整 Python 测试
**1123 passed in 337.81s**；Ruff lint、format（221 文件）和 mypy
（110 源文件）通过。覆盖当前 Operator 可选观测输入、观测关联、Release 组装证据、
SAM-only profiles 与双 HTTP 服务 CPU 发布/恢复的已登记回归。

本轮没有重新运行前端浏览器、构建或 GPU 验收。之前的真实浏览器配置编译仅证明
启动与绑定，不能替代待完成的真实选区→远程推理→发布完整浏览器验收。

多输入运行绑定已在 Core/HTTP API 层接入：完整端口映射按契约固定并纳入创建幂等摘要，
旧单输入入口保留。前端已支持多个标量 Artifact 输入及 RGB/RGBA/mask 上传，
集合和 StructuredValue 输入仍不支持。真实后端编译就绪检查与引用启动入口共享
标量载体检查；CPU 示例已注册 mask 算子。

多输入接入检查：38 项 Core/编辑器/历史契约测试通过；另一次真实 HTTP 集成
4 项通过，覆盖原 RGBA 和 RGB+mask 两条路径到发布下载，以及远程失败后的
显式重试。此处 HTTP 为实际监听服务，Shape 为 CPU 测试替身，未运行 GPU。
先前浏览器单次 31 项通过，但使用模拟 API；不将其表述为真实浏览器到模型验收。
本轮 Ruff lint/format、mypy 和 diff 检查通过，尚未重跑完整 Python 套件。
历史契约测试继续使用原摘要：重建历史目录时排除新增 mask 算子，不能更新旧摘要
来掩盖基线变化。

## 多输入内容边界补齐

多输入 `input_refs` 与旧输入入口现共用 Artifact 端口、证据闭包和 RGBA 内容校验，
全透明 RGBA 不再能通过更换请求字段绕过前景检查；恢复 20 MiB 的已导入 RGBA
输入大小限制。mask 合成拒绝透明色键、彩色/带 alpha 的 mask 以及伪装成 PNG 的
其他编码，避免隐式转换丢失语义。上传的一位 PNG 保留实际 channel_layout="1"。

验证分次执行：32 项编辑器/远程配置/mask 测试通过；随后 25 项 mask/实际 HTTP
发布/编辑器测试通过；补充的一位 PNG 和透明色键上传回归单项通过。Ruff、mypy、
diff 检查通过。没有新增 GPU 或浏览器真实后端验收，未声称完整 Python 回归通过。

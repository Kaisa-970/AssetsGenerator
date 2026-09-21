# 节点编辑器 B2 历史验证记录

此文件归档各次代码基线下的验证，不代表当前代码已重复通过这些检查。
当前能力与缺口见 [状态表](node-editor-b2-status.md)。

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

## 双输入真实浏览器 CPU 验收

代码基线 `bd048b5`，使用重新构建的前端及 `examples/cpu_image_editor.py` 实际 HTTP
服务，Chromium 执行 `frontend/smoke/image-mask.cjs`，未拦截或模拟 API。
4×4 RGB 与含前景/背景的灰度 PNG 分别上传；后端编译就绪，完整 `input_refs`
固定到运行；RGBA 在浏览器正常解码；显式恢复前后 node_states 完全一致，只有
一次 attempt，浏览器没有 pageerror。另从 Store 重新解码输出，确认全部 16 个
像素的 RGB 保持不变、alpha 与 mask 完全一致，浏览器 alpha 也逐像素一致。

仓库外证据记为 `<MASK_BROWSER_ROOT>/mask-browser.json`、`mask-browser.png` 和
`pixel-verification.json`，不提交输入、Store 或生成图。服务已正常关闭。
前端构建通过；仍存在已有的 bundle 大小提示。这次未运行 GPU、未做服务重启
验收，也没有重跑完整 Python 或浏览器回归套件。

## 多输入上传更换与迟到响应

选择替换文件时立即清除该端口旧引用；服务拒绝新文件后启动按钮保持禁用，其他
未更换端口仍保留。修改 Pipeline 输入契约会清除多输入绑定，上传响应同时核对
输入契约修订，禁止把旧契约下的迟到响应绑定到新图。新增浏览器回归覆盖成功提交、
替换 mask 失败，以及挂起上传期间添加输入后迟到响应被拒绝。
定向 Playwright 1 项、Vitest 27 项、TypeScript/Vite 构建、diff 检查通过。
本轮没有运行 Python 回归或 GPU，构建保留已有 bundle 大小提示。

## 显式复用历史输出

新增只读 references 端点，验证运行归属、节点成功及输出证据闭包后提供精确
ArtifactRef 与 kind/schema。多输入面板可将当前所选历史运行的可查看输出显式绑定
到 kind 匹配的输入；schema 不匹配或请求期间输入契约变化时拒绝。不会自动创建
运行或复用人工决定，启动仍执行端口及关系校验。当前不支持集合/StructuredValue。
验证：21 项 mask/编辑器测试、4 项实际 HTTP 集成、1 项定向 Playwright 通过；
TypeScript/Vite 构建、Ruff、mypy、Prettier、diff 检查通过。浏览器测试使用模拟 API，
不宣称真实浏览器到 references 端点的完整验收；本轮未运行 GPU。

## 历史输出复用真实浏览器验收

基线 `b2d96cf`，实际 Chromium → CPU 示例 HTTP 服务，无 API 模拟：浏览器创建
编码运行，随后从输出列表将其 RGB PNG 引用绑定到双输入 mask 图；编译、mask
上传、显式创建第二运行和 RGBA 预览通过。第二运行 named_actual_inputs 及
composite resolved_inputs 均等于第一运行输出引用；绑定期间仍只有一个运行，
新运行成功后第一运行整个 BuildRun 与之前完全一致，两个节点各一次 attempt。
浏览器无 pageerror。脚本为 `frontend/smoke/reuse-output.cjs`。

证据位于 `<REUSE_BROWSER_ROOT>/reuse-browser.json` 和 `reuse-browser.png`，
未提交输入/Store/生成资产。第一次脚本因全局 status 定位匹配多个提示而失败，
改用 footer 状态定位后在全新目录完成验收，保留第一次证据。两次临时服务均正常
关闭。本次仅 CPU 浏览器验收，不证明 GPU 模型质量或服务中断恢复；未重复运行
全仓回归，Prettier 和 diff 检查通过。

## 单图历史输出入口

单图 image 表单新增历史输出来源，复用既有只读 references 校验端点和 image_ref
创建协议。点击“用作输入”不会上传/派发，明确启动才发送已验证引用；输入契约
变化后清除绑定。当前适用于 RGB/RGBA image，不扩展 observations 或集合输入。
验证：2 项定向 Playwright（模拟 API）通过，覆盖精确 RGBA 引用提交、绑定不启动、
不重复上传、改为 RGB 契约后禁止启动和已有多输入路径。27 项 Vitest、前端构建、
Prettier 与 diff 检查通过。未做此新增入口的真实 GPU/浏览器后端联合验收。

## 运行切换时的输入绑定隔离

运行面板切换当前选中的历史运行时，会清空多输入 ArtifactRef 与单图历史 image
引用；旧运行的输出按钮不会把引用带入新运行。新增 Playwright 回归证明 A 运行
绑定后切换到 B，所有输入为空且启动按钮保持禁用。Vitest 27 项、前端构建、
Prettier 和 diff 检查通过。

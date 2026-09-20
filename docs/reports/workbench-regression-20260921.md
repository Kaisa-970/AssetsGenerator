# 工作台回归检查 2026-09-21

完整 Python 回归在 `75ce34f` 基线上启动：1165 passed in 320.23s。
该轮执行期间仅新增独立 ComfyUI 模块、测试和设计说明；pytest 收集时未包含这些
新测试，不能把全量结果用于宣称 ComfyUI 已通过完整验收。

同一基线 Ruff 格式检查（221 文件）、Ruff lint、mypy（110 文件）通过。
后续新增 ComfyUI 单次提交日志和 HTTP transport 合跑 10 项测试通过，
新增文件 Ruff 检查和 mypy（112 文件）通过。

完整日志在仓库外 `/tmp/workbench-full-20260921.log`，不是持久发布证据。
测试覆盖既有 Core、DAG、编辑器后端、恢复与发布，不替代真实模型质量评测。
近期真实浏览器发布、执行器中断与下载证据见各自专项报告及指南。

## ComfyUI 接入后的全量回归

在 `4803e80` 启动完整 Python 套件：**1294 passed，1 failed，356.87s**。
唯一失败为历史契约 fixture：它从当前 OperatorSpec 重建旧版本时，未排除新增
image_transform@1，因此旧注册表摘要变化。`1eeaae6` 修复 fixture，保留所有
历史预期摘要；同时扩展“无关新增 Operator 不改变既有管线契约摘要”的断言。
修复后定向运行 tests/test_compilation_compatibility.py：**4 passed，2.06s**。
没有重新运行完整 1295 项，不把两次结果合写成“全量全部通过”。

同轮 Ruff lint、格式检查（260 文件）、mypy（128 source files）通过。
python -m build 成功生成 sdist/wheel；检查 wheel 包含 ComfyUI 服务 CLI、注册模块、
image_transform OperatorSpec 和构建后的编辑器资源。构建产物未提交。
上一轮前端 build、16 项单元测试、15 项浏览器回归及 ComfyUI 目录/Backend 切换
实际编辑器 smoke 通过，详见设计文档对应记录。

目前已有 CLI、服务执行器、双节点 DAG 协议联调与编辑器配置接入。尚未运行真实
ComfyUI 节点/模型，尚未完成浏览器发起 ComfyUI 推理到输出的完整验收；上传早期
中断仍采用保守阻塞，不支持任意中断自动续跑。这些边界不因回归检查而关闭。

## 图片节点与预检补齐后的全量回归

代码基线 e7f5dd0，测试期间仅更新文档，未修改实现或测试。
单次完整 Python 回归：**1306 passed in 355.44s**。
这次确实重跑了完整套件，包含历史契约 fixture 修复、显式 encode_png 节点、
统一编辑器图片导入、ComfyUI profile/服务/DAG/预检相关测试。

Ruff lint、format（265 文件）及 mypy（130 source files）通过；
sdist/wheel 构建通过。独立检查 wheel 内 comfy_preflight、dag_image_encoding、
OperatorSpec 与源码字节一致，并包含当前图片预览前端资源。
构建产物未提交。

最近一轮前端在图片预览提交前执行：16 项单元测试、16 项 Playwright 测试、
TypeScript/Vite 构建通过；本次未重复运行前端测试。随后实际编辑器 HTTP
预览及损坏输出拒绝读取的 smoke 已通过，见 ComfyUI 专项设计记录。

ComfyUI 浏览器创建→服务执行→输出→完成后恢复的 CPU 协议链现已跑通，
但其上游仍为注入 fixture，不代表真实 ComfyUI 部署或模型验收。
本轮没有运行 GPU，不新增重建质量、纹理质量或推理中断结论。

## 模块配置与 CPU 画布示例后的回归

在 `54ebf3a` 启动完整 Python 套件，运行期间只修改文档：
**1323 passed，4 failed，403.37s**。四处失败均位于
`tests/test_editor_input_contract.py`：空图启动门槛被放进入口契约校验函数，
遮住了原本独立的输入 schema 检查。随后分离输入契约检查与完整启动门槛；
编辑器编译和创建共用完整门槛，空图仍不允许启动。

同基线前端单次执行 **19 项 Vitest、22 项 Playwright 全部通过**，
TypeScript/Vite 构建通过；main/GLB chunk 仍有超过 500 kB 的构建提示。
Ruff lint、format（269 文件）、mypy（130 源文件）通过。
sdist/wheel 构建通过，核对 wheel 的执行入口、PNG Adapter、OperatorSpec
及四个编辑器资源文件与源码逐字节一致。此包构建发生在校验函数拆分之前，
不能用于证明之后源码的打包结果。

本轮没有 GPU 或真实 ComfyUI 验收。CPU 画布浏览器脚本在上一提交验证过
上传、节点输出预览及刷新恢复；不将其归入本轮 22 项路由 fixture 浏览器测试。

修复后定向重跑输入契约、CPU 示例、编辑器后端与执行入口：**31 passed in
22.17s**；再次运行仓库 Ruff lint/format 与 mypy，均通过。本次没有第二次
完整 Python 回归，不将 1323 项与定向结果相加宣称全量通过。

## 手工画布与多实例布局回归

实现基线 `566c477`，补充连续添加同一 Operator 的浏览器测试：两个实例 ID
独立，保存后的布局不同，所有节点矩形不重叠，输入绑定没有被隐式复制。
前端单元测试 **26 passed**。第一次完整浏览器回归 28 passed / 1 failed：
原固定运行图测试在新增节点自动 fitView 完成前捕获了旧视口。改为等待新增操作
的 scale=1 后再检查运行图与编辑图隔离；随后单次完整回归 **29 passed in 33.6s**。
本次没有修改产品实现，也没有重跑 Python 或 GPU。

另有实际 CPU 服务浏览器验证：从空画布配置输入、添加 encode_png、连线、
编译、上传、执行、预览与显式恢复成功；成功节点 attempt 数保持 1。
可重复脚本见 add-dag-module 指南，使用真实 Core/Store/HTTP，但不涉及模型。

## 共享图片输出损坏的恢复边界

在 `6abd50a` 后补充 CPU 示例服务回归：JPEG 输入扇出两个 encode_png 实例，
成功后关闭 Repository；确认两个输出共享 ArtifactRef，再删除隔离 fixture 的
PNG Blob 并重新打开服务。显式恢复将两个节点均记为 recovery_blocked，历史
attempt 原样保留，原 JPEG 输入摘要仍有效，PNG 未被重建，输出入口拒绝读取。
测试同时拦截执行器，禁止恢复通过重新运行 Adapter 修复缺失证据。
`tests/test_cpu_image_editor_example.py` 的三项测试通过；Ruff lint/format 通过。
本次没有修改 Core 行为，没有新增 GPU 或推理中断验收结论。

## 当前基线完整回归（d2fb689）

单次完整 Python 套件 **1328 passed in 442.94s**。运行期间仅修改状态文档，
没有修改实现或测试。这次覆盖此前输入校验与启动门槛拆分的修复，不再依赖
上轮全量失败后定向测试的组合推断。

前端 **27 项 Vitest、30 项 Playwright 全部通过**；TypeScript/Vite 构建通过。
Ruff lint、format（269 文件）、mypy（130 源文件）通过；sdist/wheel 构建通过。
检查 wheel 的编辑器执行模块、OperatorSpec 及全部编辑器静态资源与当前源码
逐字节一致。大于 500 kB 的 main/GLB chunk 提示仍存在；构建产物未提交。

本轮没有运行 GPU 或新增真实模型验收。真实父 DAG 故障重试、多图浏览器全链、
真实双模型比较与 ComfyUI 部署仍按 [B2 状态表](node-editor-b2-status.md)推进。

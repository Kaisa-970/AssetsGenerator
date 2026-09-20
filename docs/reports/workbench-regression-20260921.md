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

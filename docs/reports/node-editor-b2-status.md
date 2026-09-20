# 节点编辑器 B2 状态核对

日期：2026-09-20。此文核对当前实现与设计第 15.5 节，不宣称项目或完整 B2 已完成。

| 要求 | 当前证据 | 尚需工作 |
| --- | --- | --- |
| 从画布创建固定计划与运行 | node_editor 编译/绑定；单图真实 smoke；多视图真实 DA3/Open3D smoke | 更多组合的验收 |
| 人工等待与确认 | 实际 iframe CPU smoke；真实单图 mask 确认；耐久决定回执回归 | 更多人工 Operator |
| 重启、显式恢复与重试 | 单图服务重启记录；DAG 中断报告；多视图同服务恢复 | 不把同服务恢复当作多视图服务崩溃验收 |
| 结果预览 | 固定运行图；经过证据验证的 asset/release/qa/GLB 输出链接 | 画布内 GLB 预览已实现并通过真实顶点颜色模型 smoke；内嵌 PNG 纹理像素检查、WebGL 不可用及关闭/切换回归已补齐；复杂材质/压缩扩展尚未覆盖 |
| 输入体验 | 浏览器单图上传、多图组包；精确引用绑定；创建请求幂等重试 | 多图上传到真实模型的完整浏览器链待补独立验收 |
| 保留既有入口 | 固定 workbench、单图 CLI、多视图 demo 均保留 | 未授权替换或删除旧入口 |
| 模型可配置 | 逐节点具名本地 profile，模型/环境身份核验 | 同图两个真实模型、远程 HTTP/ComfyUI 未验收 |

相关证据见 [B2 实现记录](node-editor-b2.md)、[真实单图画布验收](node-editor-b2-real-smoke.md)、[多视图迁移](generic-dag-multi-view.md)。真实运行数据位于各报告声明的仓库外目录，不提交模型、Store 或生成资产。

实际结果预览及其交互回归已经补齐。当前进入远程 Backend 协议与持久化基础实施：幂等 submission、job lookup、响应丢失、超时、输入上传及固定结果下载已有本机 HTTP 测试；受信 remote DAG、显式输入上传及 CPU 图像导入已接实验路径；耐久本机 HTTP 服务和显式 worker 已与菱形 DAG 联调，覆盖服务重开和完成后离线核验。真实模型进程服务、生产目录和 UI 仍未开放。下一切片为独立环境模型进程门控，状态与验收规则见[远程 Backend 设计](../design/remote-backend-v1.md)。不能把现有本地进程退出规则直接套到远程作业，也不能用一次 HTTP 200 代替模型身份及输出证据验收。

## 本次回归

在多图上传提交 `1abadca` 后启动完整 Python 回归；测试过程中只有前端格式整理及文档变化，没有修改 Python 实现。单次结果 **961 passed in 249.86s**。格式整理后的前端 Playwright **12 passed**，TypeScript/Vite 构建通过；Ruff lint/format（174 文件）、mypy（87 源码文件）、Prettier 指定三文件检查通过。此轮未执行 GPU，不能替代上表未完成的验收。

## 远程协议基础加入后的全量回归

在 `9e0d018`（严格远程 HTTP JSON 解析）上执行单次完整 Python 测试：**1026 passed in 235.85s**。测试期间只修改设计与状态文档，未修改 Python 实现。仓库级 Ruff format（184 文件）、Ruff lint、mypy（92 源文件）、sdist/wheel 构建及 git diff --check 通过。此次没有重跑前端浏览器测试或真实 GPU 推理；远程调度与真实服务仍未验收。

## 耐久远程服务联调后的全量回归

在 `f2a19fd` 上运行单次完整 Python 回归：**1051 passed in 256.14s**。运行期间仅编辑文档，没有修改 Python 实现或测试。Ruff lint/format（194 文件）、mypy（97 源文件）、sdist/wheel 构建与 git diff --check 通过。未重跑浏览器测试或真实 GPU 推理。远程 CPU 菱形、服务重开和 worker SIGKILL 不重放已有测试，真实模型进程门控与自动恢复仍待实现。

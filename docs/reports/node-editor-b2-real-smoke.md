# B2 画布单图真实运行验收

本轮使用已安装 SAM / TRELLIS.2 环境和本地权重，从浏览器画布启动单图模板，经过 mask 审查、生成、发布和输出读取。没有下载 PyTorch 或模型，没有修改模型代码或降低身份校验要求。

## 实际运行

- 父运行：`dag_f830869cbf2f4f91a1c83b7c3c40e26a`。
- `candidates`、`choose_object`、`generate_asset` 全部成功，每个节点恰好一次 attempt。
- 通过真实 React 页面加载模板、编译、填写服务端图像路径并启动；通过页面打开独立 mask 审查入口、预览并确认；通过画布输出链接读取 GLB。
- 检查人明确为 `Codex automated B2 smoke (not user approval)`。本次决定是自动化验收，不代表用户本人认可选区或模型质量。
- 采用 robot 输入和 SAM 最大背景候选取反，未启用最大连通区域清理。预览截图确认主体覆盖；沿用前次单图验收的选择设置。
- 重新加载 GLB：180,268 顶点，281,723 三角面。asset/glb/qa/release 四个 HTTP 输出均可读取。
- 从父 BuildRun 引用验证完整 Artifact 证据闭包通过。QA 的可加载、非空有限网格、digest、空间契约和必要 provenance 均通过；相对尺度和估计 forward 为 warn。

## 启动检查

此前约五分钟仍在模型摘要计算的尝试没有被改写为成功。本次重新启动时，在进程外验收脚本中包装计时函数，未改变摘要结果或身份契约：SAM checkpoint 3.32 秒，SAM 环境 12.13 秒，DINO snapshot 1.72 秒，TRELLIS.2 snapshot 56.29 秒，TRELLIS 环境 30.44 秒。随后 HTTP 服务成功监听。

观察：约 14.8 GB 的 TRELLIS 权重摘要及环境核验占主要启动时间。此次未复现上轮五分钟耗时，不能据此归因于某一个确定的系统原因，也没有跳过哈希来换取启动速度。

## 服务重启

完成发布后正常关闭本次服务，再用未加计时包装的普通 CLI、相同目录/Store/profile 启动。首次 GET 的完整 BuildRun 与关闭前逐字段相同；随后显式 resume 成功，node_states（包含 attempt 与 worker）、receipts、named_actual_inputs、plan/plan_id 和输出列表均保持相同，没有重新推理。两次真实 Backend 的 worker exit_code 均为 0。

重启后的浏览器重新打开运行并读取 GLB 链接：HTTP 200，16,397,488 字节。`reopened.json`、`restored.json`、`restart-check.py` 和 `browser-result.json` 保存对应证据。恢复会增加持久 revision，因此不声称显式 resume 后整个 BuildRun 字节不变。

## 证据与复现

外部根目录记为 `<B2_VALIDATION_ROOT>`，仓库不提交输入、Store、生成文件或本地路径配置。

- `service.log`、`restart.log`：启动、阶段计时和 HTTP 请求记录。
- `canvas-smoke.cjs`、`review-smoke.cjs`、`confirm-smoke.cjs`、`result-smoke.cjs`：实际浏览器操作脚本。
- `created.json`、`preview.json`、`decision-response.json`、`completed.json`：运行与决定快照。
- `verify.py`、`validation.json`：闭包、输出读取及网格回读检查。
- `canvas-created.png`、`review-selected.png`、`canvas-completed.png`：页面截图。

启动命令见 [节点编辑器指南](../guides/node-editor.md)。脚本内本机路径仅用于本轮环境，复现时须替换为自己的已安装模型配置与输入。

## 范围

本轮验证单图真实流程，不代表质量 benchmark。逐节点 profile、输入上传、图内状态视图、内嵌审查、HTTP/ComfyUI 和多视图画布执行仍未完成，完整 B2 不据此关闭。本次未在推理中注入服务崩溃；此前 DAG 进程异常验收见 [运行中断报告](generic-dag-interruption.md)。

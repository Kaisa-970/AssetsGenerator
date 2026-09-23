# TripoSR 服务发现与真实资产发布验收

日期：2026-09-23。状态：新服务发现入口单模型真实 smoke 通过；未提交工作区验收。

## 已验证

复用既有 TripoSR checkout、权重、TRELLTS 独立环境和 frame validation 证据，
没有下载模型、安装 PyTorch 或修改模型代码。服务使用现有 ShapeServiceHandler 与
ServiceProcessWorker；本次不是新 ShapeModelService 同步 callback 的真实模型验收。

浏览器填写 HTTP 地址、检测、确认添加；模型立即出现在目录并可加入画布。
使用原有五节点 YAML 连线，仅替换 shape 的 Backend 为刚注册的 discovered_shape 实现，
上传与历史双模型验收相同的 RGBA，执行 shape → canonicalize → quality → assemble → publish。
运行 `dag_600a19972511490f82341f9ffbbb8017` 五个节点全部成功，各一次 attempt。

独立核查：

- 发现声明与实际 native_frame 均为 `triposr_glb_native / +Z / relative_unit`。
- 请求固定的 Backend digest 与注册声明一致，上传 Artifact 与 shape 实际输入一致。
- 最终 GLB 可加载：42,257 顶点、84,383 三角面，保留 vertex colors。
- QA 的可加载、有限非空几何、摘要、空间契约、mandatory provenance 均通过；
  相对尺度等警告仍保留，不将工程成功解释为米制或视觉质量通过。
- 显式恢复后完整 node_states（含 attempts 和输出引用）不变，没有重复推理。
- TripoSR 无效的 seed/pipeline_type 在发现参数表中固定为 42/512。

相关 36 项 Python 测试通过；限定模块 Ruff/mypy、前端构建、git diff --check 通过。
没有运行完整测试集，不重述上一轮远程超时测试为“已确认旧问题”。

## 证据和限制

仓库外 `<DATASET_ROOT>/triposr-discovery-real-v1/` 包含 profiles.json、browser.cjs、
submitted.json、completed.json、restored.json、catalog.json、validation.json、quality.json、
verify.py、浏览器截图与 GLB、executor.log 和独立服务数据库/Store。

模型执行由服务端显式 drain 触发；不是点击一次自动领取全部任务。该边界与旧受控
服务一致。本轮未验证推理中断、监听进程 SIGKILL 或错误输入真实 GPU 行为；完成后恢复
不等于推理中断恢复。没有通过新入口同时重跑 TRELLIS.2，旧双模型报告仅作为输入基线，
不宣称本轮完成双 Backend benchmark 或质量排名。

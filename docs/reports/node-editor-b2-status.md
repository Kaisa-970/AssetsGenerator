# 节点工作台当前状态

更新：2026-09-29。推荐从 [中文使用指南](../guides/node-editor.md) 进入。
画布使用 React Flow；CLI 保留为自动化和诊断入口。下表区分真实模型验收与 CPU 回归，
不以流程可运行代表质量达标或完整 B2 完成。

| 能力 | 当前已验证范围 | 证据 / 剩余边界 |
| --- | --- | --- |
| 画布编排与执行 | 固定计划、逐节点绑定、人工选择、精确输出引用 | [B2 实现](node-editor-b2.md)；不是任意 paper workflow 都可直接连接 |
| 单图资产链 | SAM 选择 → TRELLIS.2 → 发布，真实浏览器链 | [浏览器验收](sam-only-browser-real-complete.md)；质量 benchmark 待做 |
| 双模型分支 | 同一 RGBA → TRELLIS.2 / TripoSR，真实 API 和浏览器发布 | [API](dual-shape-real.md)、[浏览器](dual-shape-browser-real.md) |
| 多视图 | 上传双图 → DA3 → Open3D → 发布；完成后重启恢复 | [真实验收](multiview-browser-upload-real.md)；[DA3 推理中主控中断已验收](multiview-interruption-real.md)；Open3D 阶段中断未覆盖 |
| 本地推理中断 | TRELLIS.2 执行器中断、孤儿门控、显式重试 | [执行器验收](trellis-executor-interruption.md)、[父 DAG 重试](parent-dag-retry-real.md) |
| HTTP 服务中断 | 真实模型执行时监听服务 SIGKILL 后重连及原作业发布 | [HTTP 验收](http-listener-crash-real.md)；不证明主机或 GPU 驱动故障恢复 |
| 输出与输入复用 | GLB 预览、下载、标量 Artifact 输入、历史输出复用 | [历史记录](node-editor-b2-history.md)；复杂压缩材质、集合表单未覆盖 |
| 交互设计首片 | 独立 QA、资产边界和人工决定展示；无模型 CPU 图片入口 | [定向验证](node-editor-product-cpu-smoke.md)；[预检与启动复核已接入](node-editor-preflight-cpu-smoke.md)，普通新运行已改为一次点击自动预检和创建；[继续提取与操作提示](node-editor-continuation-actions.md)已接入；用户已反馈确认页面走查；六步资产主路径预算仍待补验 |
| 参数与结果交互 | 未应用参数门控、历史徽标来源、图片放大/收起、中间网格 A/B、输出列表拆分 | [本轮验证](editor-usability-20260924.md)；真实模型质量与六步完整资产路径仍待用户验收 |
| 模型服务发现 | URL 添加 Shape 服务、已知 shape capability 独立安装与绑定；同节点切换 TripoSR/TRELLIS.2、自动领取 | [真实验收](dual-backend-discovery-real.md)；服务间 GPU 串行由操作者保证 |
| 默认工作区 | 画布为主体、目录默认收起、快捷添加节点；完整浏览器回归 107/107 | [最新冻结基线](node-workspace-v2-progress.md)；未据此宣称最新界面 GPU 验收完成 |
| ComfyUI | 实验协议及 CPU 浏览器验证 | **真实部署验收延期**；有实际使用需求后再启动 |

本轮已整理共享网格读取、provenance、release 边界，见 [改动与验证](shared-foundations-20260921.md)；
没有重写执行器或删除旧 CLI。
本轮已补 DA3 推理中主控中断的独立脚本验收，见上表；不扩张为所有阶段故障恢复。
Fire3D、其他新增模型与界面功能不属于本轮。

完整回归记录见 [2026-09-21 回归](workbench-regression-20260921.md)；
各轮测试计数、CPU 浏览器与历史输出复用细节见 [历史验证归档](node-editor-b2-history.md)。
新验收应写专项报告，再更新本表，不向本表追加历史日志。

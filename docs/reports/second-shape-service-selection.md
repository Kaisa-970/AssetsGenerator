# 第二个真实 Shape 服务：选型与验收范围

日期：2026-09-23。状态：选型；本轮尚未完成新服务入口的 GPU 验收。

## 结论

优先复用 TripoSR 的既有环境和权重，通过新的服务发现/包装入口验证接入，与已有 TRELLIS.2 使用同一 RGBA 输入和发布链比较。这是第二个真实 Shape Backend 对新入口的验证，不是新增算法。旧双模型浏览器验收见 [既有报告](dual-shape-browser-real.md)，不能替代这次入口验收。

本地已确认 TripoSR 权重约 1.6 GB，已有独立模型环境可复用，不安装或下载 PyTorch。现有 Core 输出声明为 `triposr_glb_native`、`+Z`、`relative_unit`；必须复用已验证坐标规则，不能根据 GLB 格式猜测 Y-up。检查时设备为 8 GB RTX 5060 Laptop，空闲约 4.9 GB，资源随系统使用变化；不保证默认约 6 GB 推理配置能运行，不擅自终止其他进程。

## 候选与边界

- [TripoSR](https://github.com/VAST-AI-Research/TripoSR)：官方默认约 6 GB 显存；已有部署与坐标证据，接入成本最低。输出顶点颜色，不视为高质量 PBR 材质生成。
- [Stable Fast 3D](https://github.com/Stability-AI/stable-fast-3d)：如果需要全新模型，优先考虑。原生 GLB/UV/材质输出；约 6 GB 显存，但需额外编译组件、申请或接受权重访问条款及遵守模型许可。不是本轮必须引入的依赖。
- [InstantMesh](https://github.com/TencentARC/InstantMesh)：包含多视图扩散与重建，部署与资源成本较高，不符合这轮最小验证目标。
- [Hunyuan3D-2](https://github.com/Tencent-Hunyuan/Hunyuan3D-2)：mini 的参数规模不等于整个服务的显存需求；纹理链更重，暂不选。
- [Shap-E](https://github.com/openai/shap-e)：较旧，常规示例输出需转换；无充分依据仅凭依赖数量判断它更省显存。

## 下一步执行与验收

1. 固定并记录 TripoSR 权重、代码、环境和启动回调摘要；复用环境，GPU 串行。包装器的 deployment 是部署方声明，不自动证明实际模型身份。
2. 在不增加 Core 模型分支的前提下，使用 `ShapeModelService` 接入真实同步推理；参数只暴露实际生效的配置，不伪装通用 seed 有效。
3. 在同一个 `shape_generation@1` 节点分别选择 TRELLIS.2 和 TripoSR，输入 Artifact 保持相同。SAM3D 的 `masked_shape_generation@1` 不是该节点的可互换实现。
4. 比对发现页声明、实际 native_frame、参数、Backend 身份、输出证据，再跑 canonicalize → QA → assemble → export。当前发现声明仅展示，需显式核查与输出一致，不把展示当成已强制执行的保证。
5. 验证 GLB 外观保留、单位状态、失败可见性、完成后恢复不重复推理；推理中断未知状态必须阻塞，不能自动重提。
6. 同输入的结构/QA/外观结果并排记录；相同计时边界方可比较耗时。工程成功与视觉质量分别报告，不以 CPU fixture 或两例 smoke 宣称质量 benchmark 已完成。

本轮不扩展新 Operator、通用服务类型或自动部署；不宣称新入口真实模型验收已完成。

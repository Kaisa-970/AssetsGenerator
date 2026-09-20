# Generic DAG 多视图迁移切片

日期：2026-09-20。三节点迁移：geometry → reconstruction → release。调度器未增加 workflow 名称分支；模型与发布实现由 AdapterRegistry 绑定。

## 实现

- 专用 OperatorSpec YAML 定义端口；适配器不复制端口。geometry 输出 cameras/depths/points 和固定 GeometryFrontendEvidence，reconstruction 输出完整 ReconstructionEvidence。
- 结果证据保存 observation/上游结果的精确引用、原 Backend metadata、profile 摘要、父 run/node/attempt。关系 validator 在派发前验证 view、尺寸、camera/world frame、unit，以及完整来源引用链；不支持隐式空间转换。
- geometry 与 reconstruction 分别通过已有独立 Backend 环境及 DAG gated worker 执行。release 是 CPU 节点，通过 owned 子运行复用原 canonicalize、组件 provenance、QA、材质导出和原子发布链；明确标记 reused_input，不再次推理。
- prepared 工作流入口也逐字段验证结果与证据一致，禁止用任意现存 Artifact 冒充来源。固定证据进入发布子 BuildRun inputs 和细粒度 provenance。
- 每个阶段独立登记子 BuildRun。恢复先验证引用闭包，再只读比对结果、输入、实现身份与发布文件。恢复时不重新写结果证据。
- 默认 relation registry 及其实现身份保持原样；多视图入口显式注册新关系，避免改变已保存单图计划的身份。

## 验证范围

回归覆盖各模型仅执行一次、发布失败后仅重试发布、三个阶段的子成功/父保存中断恢复、证据缺失、恢复输出与固定证据不符、外观参数变化、预检查失败审计、相机/深度空间关系和不同观测串线。CLI 以 CPU contract Backend 验证 start/resume；不将该测试称为真实模型验收。

## 限制

这是三个节点的迁移切片，发布内部步骤仍为复合操作；未将 canonicalize/QA/assemble/export 分别开放为任意组合节点。只支持显式本地资源，不新增 HTTP/ComfyUI。

现有通用引擎将 adapter.recover 的语义拒绝记录为 failed，而缺失/损坏 Artifact 由证据扫描记录为 recovery_blocked。两者都不会自动重复推理，显式 retry 新建 attempt；本轮保留原状态语义，没有为多视图修改 Scheduler。

首版关系校验允许观察集合的相机子集及空 depth 集合，以兼容既有 Backend 契约；Open3D 对实际所需深度另行严格检查。它证明引用与空间声明一致，不证明相机估计精度或任意外部证据的真实性。

## 真实 DA3/Open3D 验证

使用既有 DA3 与 sugar 环境、DA3-Base 本地 snapshot，输入沿用 SOH 两张图；没有安装或下载包/模型。运行 `dag_17fc5115be514d6fbd647b3c30ac9ac9` 成功，geometry/reconstruction/release 各一次 attempt。DA3 与 Open3D 各一条 gated worker，正常退出。发布 GLB 回读得到 70740 个顶点、96610 个三角面、35651 种顶点颜色，全部子结果证据闭包校验通过。

随后从新 Python 进程执行 resume：仍为 succeeded，node_states、node_attempts 完全一致，worker 与输出引用未变，没有重复推理。

仓库外证据：`<DATASET_ROOT>/dag-multiview-20260920/` 下 `start.log`、`completed.json`、`resumed.json`、`validation.json`、`store/` 和 `service/executions/`。这是固定双视图配置的功能 smoke，不是代表性物体质量 benchmark；多视图推理中 SIGKILL 未在本轮重复实测，共享进程层的 TRELLIS2 实测另见 [中断报告](generic-dag-interruption.md)。

## 检查记录

- 全量运行收集 907 项：905 passed、2 failed（770.77 秒）。两项失败均是新增审计测试误用 `contract_invalid` 作为错误码，实际既有契约为 `contract_error`；断言已修正。
- 修正后独立审计文件 4 passed，随后多视图 DAG 集成及审计合跑 10 passed（52.99 秒）。未重新运行完整 907 项，不将分轮结果相加宣称全量一次通过。
- 新 owned/prepared 文件 25 passed；空间关系与既有关系测试合跑 41 passed；CLI stub start/resume 1 passed。
- Ruff 格式与 lint、mypy（83 个源文件）、sdist/wheel build、git diff --check 通过。

三节点本地多视图迁移已具备真实运行和恢复证据。后续应基于本报告审查里程碑 A 的边界，再启动 B1 节点目录与只读/编辑画布；尚不宣称所有 Pipeline 内部算子已经独立开放。

## prepared 发布入口的 Backend 绑定修正

结果证据新增生成时的 `backend_binding`，包含 Backend 名称/版本、Operator、节点 ID、Pipeline 名称/版本及契约摘要。公开 prepared 发布入口在处理结果之前核对绑定与传入 ResolvedPlan 一致；缺少绑定也拒绝，不将旧结果补归到当前 Backend。

回归覆盖 geometry/reconstruction 的 Backend 名称或版本变化、绑定缺失，并断言拒绝后没有 provenance 或发布目录。匹配绑定的原成功、故障恢复与 CLI 路径继续验证。

此前真实 smoke 证据是在此字段加入前生成的，保留为历史事实；本次未重新执行 GPU 验证。旧证据不能直接用于新的 prepared 发布入口，既有 DAG 的实现摘要变化也会阻止按新代码认领旧计划；不修改历史 Artifact 来绕过身份检查。

本修正验证：prepared/owned/DAG 恢复/CLI 42 passed；既有多视图 workflow 与关系校验 73 passed。Ruff 格式/lint、mypy（83 个源文件）、git diff --check 通过；未重复全量测试。

## 画布多视图接入准备：本地配置模块与具名目录

将示例中的 DA3/Open3D 资源绑定提取为正式模块 `multi_view_profiles.py`。`MultiViewProfileConfig` 为不可变配置，可由严格 Mapping 解析；未知字段、缺失路径字段、非有限/非正参数、非法 process_res/up_axis 和 TSDF truncation 关系在资源读取前拒绝。原 source/model/runner/adapter/environment 身份与资源变化 guard 保留，Backend 文件定位改为包内路径，示例通过兼容 wrapper 调用新模块。

`register_multi_view_profiles()` 为 geometry、reconstruction、release 注册默认及具名实现；示例 CLI 已使用该目录，默认 YAML 保持无显式 Backend 的旧绑定方式。具名配置不解除已有 profile 指纹与证据关系校验。本轮只是画布入口的基础模块，画布仍只接受单图输入，多视图配置文件/API/UI 尚未接通。

验证：配置/目录/demo 合跑 22 项通过；多视图目录/demo/执行/恢复审计合跑 14 项通过（两轮重叠，不相加）。Ruff 格式/lint、mypy（87 文件）通过。覆盖资源 guard、虚拟环境符号链接路径、非法配置提前失败、具名绑定及三个节点发布后恢复不新增 attempt。未下载模型或运行 GPU。

# Phase 6 CLI 收口与 Phase 7 显式场景首版

日期：2026-09-17。

## Phase 6 命令验收

统一 CLI 提供 import-observations、build-multi-view、build-candidate、review-candidate、inspect。
复用现有 DA3/Open3D/TRELLIS.2 独立环境和本地 snapshot，串行运行真实 scan65 输入，未安装
或下载 PyTorch。DA3→Open3D 运行 run_f830cdc040a745e89f04070f43f87af6 成功，随后
TRELLIS.2 候选生成成功。产物位于 `<DATASET_ROOT>/phase6-cli-smoke-v1/`。
人工服务的编辑/保存/组合路径已有前轮浏览器实测，本轮未伪造人工质量确认。
Phase 6 关闭的是流程首版，不关闭原规划中质量/completion/video/RGBD 延期项。

## Phase 7 范围及验证

- extract-scene：显式场景图与用户 mask → 串行独立对象 AssetRelease；无自动检测、分割或位姿估计。
- build-scene：精确 release 引用和显式 canonical→world 位姿 → AssetInstance/SceneDefinition、场景 GLB。
- 复用资产不写入 world_pose，允许同一资产多实例；同单位、正统一缩放/旋转/平移，拒绝
  隐式单位转换、反射和剪切。GLB 位姿使用 E*T*E^-1。
- 父子运行失败可精确回查；批次和场景目录原子发布。嵌套资产完整保留 release.files，
  校验 digest 和路径，拒绝目录穿越及文件/目录冲突。
- 独立 review 修复失败子运行关联缺口，并补齐 manifest 错误分类和完整嵌套资产交付。
  修复后独立复审无阻断问题。
- 全量 407 项测试通过后补充 CLI 场景调用测试；场景相关 10 项再次通过。
  Ruff format/check、mypy（43 个源码文件）、build 通过。

真实 CPU smoke 将已有混合组合资产放置两次，含旋转、统一缩放和平移；导出 4 个节点、
443,608 个面，保留 vertex/texture 外观，原资产不变。实例目录所有 release.files 均存在。
场景 Artifact：`sha256:7c303182f18ba357670511e78a18e7c3e301b4ce1923ff8af69bfc0478965700`。
证据：`<DATASET_ROOT>/phase7-scene-smoke-v1/` 的 layout.json、result.json、inspect.json 和 scene/。

对象 mask 提取批次通过 Fake Backend 契约/失败测试，未对真正多物体场景运行模型验收；
真实装配 smoke 复用已有资产，不证明自动 scene understanding。后续重点为真实多物体输入
验收和可替换的 detection/instance segmentation/pose Backend，物理与自动布局质量仍延期。

## SAM v1 自动 mask 候选

复用 sugar 环境中的 segment_anything 1.0 与本地 ViT-H checkpoint；没有下载或安装。
checkpoint SHA256 为 `a7bf3b02f3ebf1267aba913ff637d9a2d5c33d3173bb679e46d9f338c26f262e`。
以 VOC 2007_000121 双显示器场景执行真实 GPU smoke（points_per_side=8、max_instances=10），
run `run_a7c0fc1c4c22416a8296842a2c984ac7` 成功并产生 10 个 unknown/unreviewed mask proposals。
根据 VOC provided mask 只作诊断，两个最佳候选 IoU 分别为 0.9544、0.9447；该指标不进入
SAM 语义标签，也不代表类别检测置信度。自动化 smoke 明确选择这两个候选验证交接，
selection 保留全部未选择候选；不冒充人工质量批准。

隔离 runner/adapter/Core/selection/extraction 交接测试覆盖本地 checkpoint 摘要前后校验、环境与
安装身份、mask/area/bbox/分数、确定排序去重、路径逃逸、后端畸形响应和证据篡改拒绝。
每个 mask 具有独立的 ExecutionOutput/provenance；人工 selection 具有最小 BuildRun、user-source
provenance 和失败状态。runner 的实际 AMG 参数与配置参数分别保留，依赖身份明确限定为安装元数据。

同一选择先用 TRELLIS.2 串行提取：object_001 成功，object_002 在采样完成后的 CuMesh
`fill_holes` 报 CUDA `invalid configuration argument`。父运行 `run_9e8243ae831f47329228094a0655905c`
正确记录第一个子运行成功、第二个子运行失败及精确 child_run，未留下最终批次目录。这是当前
TRELLIS.2 后处理对该输入的 Backend 边界，不是 SAM 或 Core 证据链成功结论。

随后复用已验证的 TRELLTS 环境、本地 TripoSR 模型和 native-frame 证据，不下载依赖或权重，
对两个选择串行提取成功。父运行 `run_4f9322a55d004423a5ea42557be29564` 产生两个标准
AssetRelease：`sha256:6e4c8b535cfb1dc97e08a20574b1baa8887d3485e0409417f4f37b33d59c59ad`
与 `sha256:07cf71b284293e60eed6b923b88be1296f16e270868a834bd7126cf05900fd08`；
所有 release.files 与 Store 逐字节一致。再以显式人工位姿装配场景，运行
`run_9b13eae58ced4d9cb4af44d2b89cd485` 成功，SceneDefinition 为
`sha256:e3280a292ed9e37cb5258a4ae3f6dd4bed123555809c9c1adcea9a768fa3035c`，
GLB 为 `sha256:42bc9ea76537893e4365cdf2225394db34a99f5f671c664b82b06f4d33289dd2`；
重新加载得到 2 个几何节点、308,373 个面。证据位于
`<DATASET_ROOT>/phase7-sam-smoke-v1/`。该验收只证明 workflow、证据链和资产发布可运行，
不宣称语义检测、自动位姿、尺度恢复或视觉质量达标。

可视实例审查通过 loopback-only `review-instances` 提供原图 mask 叠加、显式勾选和顺序调整，
仍复用同一 selection 发布边界。全量 455 项测试、Ruff、mypy 和 build 通过。

## 可视场景布局

`review-scene-layout` 接收只含 release 引用的布局草稿，在同一 Three.js 视口中编辑每个实例的
XYZ、yaw/pitch/roll 和正统一缩放。服务端生成 canonical→world 矩阵，并继续调用 `build_scene`
完成 frame、unit、similarity 和发布校验。最终目录原子包含 layout、SceneLayoutReview、user-source
provenance、父 BuildRun 和完整子场景包；父运行引用精确 scene child run。

真实 HTTP smoke 复用上述两个 TripoSR release，以两侧平移布局发布成功。人工布局父运行
`run_a16a93cee10f4f4b9870f43b004ae57d`、子场景运行
`run_cace94a963f34bdb8ca955be4f267901` 均成功；SceneLayoutReview 为
`sha256:1ae078bf2ac29960dbf646065994aa6b5e007f006a86b673780dfd36172a6555`，
SceneDefinition 为 `sha256:3f97fdb2a75ec2ad7183d19c5c670bd58b430cf606cc6928fb2fe795c5319d0e`。
GLB 重新加载仍为 2 个节点、308,373 个面。该 smoke 使用自动化 reviewer 名称验证工作流，
不构成人工质量批准。全量 462 项测试、Ruff 和 mypy 通过。

## Phase 7 首版收口

独立 completion audit 发现最终 SceneDefinition 缺少自己的 ExecutionOutput/provenance。修复后
`assemble_scene@1` 显式输出 scene/glb provenance；`provenance/scene.json` 以 SceneDefinition
为 output_artifact_id，并精确派生自 SceneRequest 和有序 AssetInstance。普通 `build-scene` 与
可视布局路径均可直接回查完整组装事件。Phase 7 首版至此关闭；自动语义、自动 pose、尺度恢复、
completion、碰撞/物理和质量验收仍按文档延期，不包含在本次完成声明中。

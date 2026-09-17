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
全量 445 项测试、Ruff、mypy、build 通过。真实候选到 TRELLIS.2 的串行提取另行运行，
其结果不影响 SAM proposal Backend 的真实可用性结论。

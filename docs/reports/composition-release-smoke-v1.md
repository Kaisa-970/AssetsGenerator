# 标准组合发布与 Phase 6 首版流程里程碑

日期：2026-09-17。

## 验证事实

已接入标准 AssetDefinition / AssetRelease：独立 canonical 组件、component provenance、
原材质 GLB 导出、基础 QA、区域/检查/变换证据以及运行状态。轴转换只改变表示，保留
组合姿态与尺度；没有新增模型调用、自动配准或几何融合。

- 全量 366 项测试通过；Ruff format/check、mypy（41 个源码文件）、build 通过。
- 新增测试检查 release 文件字节/digest、资产与组件引用、材质、坐标往返、人工决定和
  变换证据、组件 provenance 对应 BuildRun 节点，以及错误 frame/unit 拒绝发布。
- 使用已有真实 DA3+TSDF/TRELLIS.2 人工区域组合再次发布，逐文件校验 release.files 字节，
  并确认 AssetDefinition 同时包含 reconstructed/generated 组件。未重新运行 GPU 模型。
- 真实 release identity：`sha256:f09d64b32f8cf424e0303caab796a1f6a846f745ceb26ddafeba4af5eedf033c`。

外部证据：`<DATASET_ROOT>/candidate-manual-alignment-smoke-v1/standard-release-smoke/composition-db708493d789419f893f2d758c365f4f/`。

## 里程碑结论

Phase 6 首版流程已闭环：观测 → 多视图重建 → 独立生成候选 → 人工对齐/检查 →
区域选择 → 组件组合 → 标准发布。此结论仅适用于既有独立环境、静态多图与人工选择路径。
基础 QA 为 warn：接缝、重叠、水密和物理质量未验证，仍不能宣称仿真可用。

完整 Phase 6 不关闭。几何约束 completion、自动配准、拓扑融合、代表性质量验收、
视频/RGBD 专项策略和通用 region-map 显式延期；Phase 5 注册/render-back QA 继续延期。

## 提交前 provenance 身份修复

多组件输出现以稳定 component_id 区分 output_id/provenance_id，参数记录 output_element_id。
回归覆盖 4 个组件 ID 唯一、按 provenance ID 建索引后准确回查对应 Artifact、身份稳定性、
分隔符歧义和标量身份兼容。上述真实 smoke 为修复前的历史证据，未改写旧产物；
修复后的身份行为由新增自动化测试验证。

修复后全量 367 项测试及 Ruff format/check、mypy、build、git diff --check 通过。

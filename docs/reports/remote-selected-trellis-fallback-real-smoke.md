# 真实 SAM 选区到 TRELLIS.2 发布 fallback smoke

日期：2026-09-20。状态：真实 SAM → 人工选择 → RGBA 准备 → 真实 TRELLIS.2 → canonical → QA → assemble → publish 已成功。
本报告证明流程和证据链可运行，不构成视觉质量或纹理质量验收。

## 运行事实

复用已有 SAM checkpoint、TRELLIS.2 独立 conda 环境和本地 4B 权重；没有下载 PyTorch、模型或输入数据。
输入为既有 robot 图片，人工选择通过工作台决定记录完成。

- 父运行：`dag_c6d889664e924a3b8a643a115909fb27`
- 状态：`succeeded`
- 外部证据：`<DATASET_ROOT>/remote-selected-trellis-fallback-20260920/`
- `candidates`、`choose_object`、`prepare`、`shape`、`canonical`、`quality`、`assemble`、`publish` 均恰好一次 attempt 且成功。
- 发布 GLB、Release Artifact 和所有上游引用的 digest 闭包均已登记并通过校验。

TRELLIS.2 sampling 成功，但 `o_voxel.postprocess.to_glb()` 在当前 CUDA/toolchain 的 `CuMesh` 后处理阶段失败。
runner 捕获明确的 `CuMesh`/`CUDA error`，使用 sampling 已产生的 vertices/faces 导出几何 GLB，并在响应中记录
`postprocess_mode=geometry_fallback_no_texture`。这保留了几何输出，但不保证纹理烘焙、洞修复或完整视觉质量；其他错误仍会直接失败。

## 验证与边界

Core 侧成功完成 shape 输出导入、空间契约、canonicalization、Geometry QA、AssetDefinition 和 GLB 发布。
QA 结果只能说明结构检查完成，不能替代人工查看。fallback 资产应视为无纹理几何交付，直到在兼容的 CUDA/toolchain 上重新通过完整 `to_glb()` 后处理。

本轮没有把自动化验收记录为用户人工批准，也没有提交外部 Store、服务数据库、模型权重或生成资产。


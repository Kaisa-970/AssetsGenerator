# Completion 生成候选准备验证 v1

日期：2026-09-17。

## 能力边界

本轮检查本地 TRELLIS.2 的公开图像生成入口，未发现重建网格条件或可见表面保留输入。
因此实现 `build_completion_candidate()`：显式选择已有 mask 的观测，通过既有
image_asset_v2 和 ResolvedPlan 生成独立候选，原重建 release 保持不变。
两份资产关联发布，不对齐、不融合、不承诺闭合，也不宣称完成几何约束 completion。
具体边界见 [候选契约](../design/completion-candidate.md)。

## 自动验证

341 项测试通过。新增测试覆盖候选 Artifact JSON 往返、原重建字节保留、generated 来源、
未知视图、路径越界和生成失败时无半成品目录。Ruff、mypy（37 个源码文件）通过。

## 真实运行

选 DTU scan65 已有重建 release 和观测 000014 的 RGB/mask，使用 TRELLIS.2 512、seed=42。
复用 TRELLTS 环境和本地 snapshot `af44b45f2e35a493886929c6d786e563ec68364d`，未下载 PyTorch。
上游工作区已有本地修改，保留原状；不声称采用 clean checkout。
证据位于 `<DATASET_ROOT>/completion-candidate-smoke-v1/`。

真实生成与候选包原子发布成功，总耗时 227.065 秒。生成 GLB 重新加载为 296422 个三角面、
texture visual；原重建为 30814 面、vertex visual，逐字节确认未改变。
两份 release 的全部物化文件与 Store Artifact 字节及 digest 一致，候选 manifest 往返一致。
证据：`result.json`、`validation.json`、`candidate/`、`source.json` 和生成分支 `run.json`。
已保存本地 TRELLIS.2 revision/dirty/source digest 作为本次运行证据；既有生成 Backend 的
provenance 仍采用原版本/model digest 记录方式，本轮不声称全面加固了该 Backend 的环境身份。

sdist/wheel build 与 git diff --check 通过。本轮没有视觉质量验收、空间对齐、区域融合或
已观测表面保持验证；仅关闭“真实独立生成候选可追溯关联发布”的最小准备步骤。

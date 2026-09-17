# 生成候选包

使用已有单图 Registry/ResolvedPlan，复用 TRELLIS.2 独立环境及本地模型 snapshot。
无需为候选流程安装或下载 PyTorch。

```python
from pathlib import Path
from assets_generator.completion import build_completion_candidate

result = build_completion_candidate(
    observations=observation_ref,
    reconstruction_release=reconstruction_release_ref,
    view_id="000014",  # 显式选择已有 mask 的观测
    store_path=Path("<ARTIFACT_STORE>"),
    output_path=Path("<CANDIDATE_OUTPUT>"),
    resolved_plan=image_generation_plan,  # image_asset_v2 的 shape Backend 绑定
    seed=42,
    pipeline_type="512",
)
```

两个引用必须属于同一 Store 且来自同一 ObservationBundle。
输出 `reconstructed/geometry/visual.glb` 和 `generated/geometry/visual.glb` 分别查看。
它们是未对齐的不同资产，不是合并结果；说明见 [候选契约](../design/completion-candidate.md)。

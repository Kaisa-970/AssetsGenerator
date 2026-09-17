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

## 显式旋转、平移和统一缩放

已有候选可直接进行 CPU 对齐，无需重新运行模型：

```python
from assets_generator.alignment import align_completion_candidate, candidate_frame
from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.models import ArtifactRef, SpatialTransform

store = LocalArtifactStore(Path("<ARTIFACT_STORE>"))
candidate = store.read_structured(candidate_ref)
transform = SpatialTransform(
    source_frame_id=candidate_frame(ArtifactRef(**candidate["generated"]["release"])),
    target_frame_id=candidate_frame(ArtifactRef(**candidate["reconstructed"]["release"])),
    # 示例：绕 GLB +Y 旋转 90°，统一缩放 1.1，再平移 (0.1, 0.2, -0.1)。
    # 这是调用示例，不是针对任意候选有效的配准参数。
    matrix=[[0, 0, 1.1, .1], [0, 1.1, 0, .2], [-1.1, 0, 0, -.1], [0, 0, 0, 1]],
)
result = align_completion_candidate(
    candidate=candidate_ref,
    transform=transform,
    store_path=store.root,
    output_path=Path("<ALIGNMENT_OUTPUT>"),  # 必须为新目录
)
```

`aligned.glb` 保留原外观；`reconstructed.glb` 与 `generated-original.glb` 是原始字节。
`transform.json`、`provenance.json` 和 `alignment.json` 保存可追溯信息；`run.json` 保存成功运行记录，失败运行可通过
`store.get_build_run(run_id)` 查询，run_id 见 provenance 或 Store 的 runs 索引。
如需交互检查，可自行在输出目录启动 `python3 -m http.server 8765 --bind 127.0.0.1`，
浏览器打开 `http://127.0.0.1:8765/`；检查后 Ctrl+C 关闭。
青色/橙色叠加只表示变换结果，不表示融合或已通过配准验收。

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

## 人工调整与确认入口

```bash
.venv/bin/python -m assets_generator.alignment_review \
  --store '<ARTIFACT_STORE>' \
  --candidate-ref '<CANDIDATE_OUTPUT>/candidate-ref.json' \
  --output '<MANUAL_ALIGNMENT_OUTPUT>' \
  --port 8765
```

打开终端显示的 `http://127.0.0.1:8765/`。页面同时显示青色重建与橙色生成，
可分别隐藏图层、绕固定 X→Y→Z 轴旋转、平移、统一缩放，也可重置变换或将模型居中。
旋转以度为单位，平移使用重建 GLB 单位，+Y 向上。旋转/缩放围绕生成模型原点；
未提供自动配准或拖拽控制柄。查看器依赖联网加载固定版本 Three.js。

点击“保存当前对齐”会调用既有对齐接口，在输出根目录创建新的 `alignment-<id>/`，
包含原资产、变换后的 GLB、provenance、BuildRun 和预览。保存后填写检查人和备注，
再确认或拒绝。修改任何变换参数会使页面上的当前保存选择失效，须重新保存后再检查。

决定作为独立 `AlignmentReview` Artifact 持久化，在输出根目录的 `reviews/` 保存 JSON，
关联精确的候选、对齐结果与变换；原发布包保持不变，其原始 pending 字段不会被改写。
检查人是自行填写的标识，不是认证身份；确认只表示人工对齐判断，不证明补全或融合。
同一对齐可追加多个决定，彼此独立保留，不自动选择某条作为权威结论。
现在可从同一 Store 恢复已有成功对齐结果后继续检查，见下节。

服务只监听本机，Ctrl+C 关闭；通过 SSH 使用时可将相同端口转发到本地后打开上述地址。

## 恢复、区域选择与组合发布

使用同一 Store 启动服务后，点击“刷新历史”，选择已有对齐结果并点击“恢复选中结果”。
历史来自 Store，所以可以跨服务会话和输出目录恢复。检查历史会显示确认/拒绝及备注。
填写检查人，选择采用的确认记录；如有拒绝意见，填写冲突解决说明，然后“选定用于组合”。
没有确认记录不能选定；选择后有新增意见则必须重新选定。

分别为重建和生成指定“全部／不保留／框内／框外”。方框最小、最大坐标使用目标 GLB 空间
（+Y 向上）；根据面中心判断归属，不切割边界面。点击“预览所选区域”查看原材质选区，
必要时“返回对齐图层”继续检查；满意后点击“组合发布”。原模型不变，新目录含 visual.glb、
composition.json、regions.json、selection.json、provenance.json 和 run.json。

组合发布现已生成标准 asset.json/release.json、geometry/visual.glb 与基础 QA；可能存在接缝、
重叠和空洞。请勿将人工确认或组合发布理解为水密、碰撞或仿真质量认证。


标准交付优先读取 release.json 中的 files。geometry/visual.glb 为统一导出入口，
geometry/components/ 保存内部坐标的独立组件；请勿将内部组件当作 +Y up 的最终导出。
qa/quality-report.json 的 warn 明确表示物理/接缝/水密质量尚未验证。

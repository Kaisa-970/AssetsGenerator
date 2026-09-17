# Phase 6 统一命令流程

所有命令从仓库运行 `.venv/bin/python -m assets_generator.cli`，安装后的入口为
`assets-generator`。下列 `<...>` 为需替换的本机路径或 Artifact ID，不是自动下载参数。
复用已安装的 DA3、Open3D 和 TRELLIS.2/TripoSR 独立环境；这些入口不负责安装 PyTorch。
所有步骤使用同一个 Store。成功构建向 stdout 输出 JSON，可重定向保存；错误返回非零状态。

## 1. 导入观测

准备包含图像、可选物体 mask 的输入清单。路径相对于清单目录；mask 与图像同尺寸，
为像素值 0/255 的二值图。生成候选所选的视图必须提供非空 mask。例如：

```json
{"views": [
  {"view_id": "front", "image": "front.png", "mask": "front-mask.png"},
  {"view_id": "side", "image": "side.png", "mask": "side-mask.png"}
]}
```

```bash
.venv/bin/python -m assets_generator.cli import-observations \
  --manifest '<INPUT_MANIFEST>' --store '<ARTIFACT_STORE>' > observations-ref.json
```

## 2. 多视图重建

```bash
.venv/bin/python -m assets_generator.cli build-multi-view \
  --observations observations-ref.json --store '<ARTIFACT_STORE>' \
  --output '<RECONSTRUCTION_OUTPUT>' \
  --da3-python '<DA3_ENV>/bin/python' --da3-repo '<DA3_REPO>' \
  --da3-model '<LOCAL_DA3_SNAPSHOT>' \
  --open3d-python '<OPEN3D_ENV>/bin/python' > reconstruction-result.json
```

`--observations` 接受引用 JSON（仅含 `artifact_id`）或 `sha256:...`。
输出 JSON 的 `release_manifest.artifact_id` 用于下一步。默认保留 Open3D 顶点颜色。
参数包含 `--da3-process-res`、`--voxel-size-ratio`、`--sdf-trunc-ratio`、
`--depth-trunc-ratio`、`--up-axis=-Y`；负轴使用等号写法。上方向是配置约定，
不是自动估计的重力。DA3 相对尺度不保证公制；TSDF 不补全未观测背面。

## 3. 独立生成候选

```bash
.venv/bin/python -m assets_generator.cli build-candidate \
  --observations observations-ref.json \
  --reconstruction-release '<RECONSTRUCTION_RELEASE_ARTIFACT_ID>' \
  --view-id '<MASKED_VIEW_ID>' --store '<ARTIFACT_STORE>' \
  --output '<CANDIDATE_OUTPUT>' --shape-backend trellis2 \
  --trellis-python '<TRELLIS_ENV>/bin/python' --trellis-repo '<TRELLIS_REPO>' \
  --trellis-model '<LOCAL_TRELLIS_SNAPSHOT>' > candidate-result.json
```

也支持现有 `build` 的 TripoSR 参数，包括必需的独立解释器、仓库与 frame-validation。
通过 `--help` 查看完整参数。候选是独立生成，不是几何约束补全；原重建保持不变。
已有候选直接跳到下一步，无需再次推理。

## 4. 人工检查、区域组合与标准发布

```bash
.venv/bin/python -m assets_generator.cli review-candidate \
  --candidate '<CANDIDATE_OUTPUT>/candidate-ref.json' \
  --store '<ARTIFACT_STORE>' --output '<REVIEW_OUTPUT>' --port 8765
```

服务仅监听本机，打开显示的地址。依次调整并保存对齐、确认、选定确认记录、
区域预览、组合发布。详细交互、坐标和冲突规则见
[候选与人工检查指南](completion-candidate.md)。Ctrl+C 关闭服务。
同一候选与 Store 重启服务后可刷新历史并恢复结果；不自动替人确认。

组合输出含 `asset.json`、`release.json`、`geometry/visual.glb` 和基础 QA。
标准 Release 的 files 字段是交付清单；未验证接缝、水密性、碰撞及仿真质量。

## 5. 查询与失败恢复

```bash
.venv/bin/python -m assets_generator.cli inspect \
  --store '<ARTIFACT_STORE>' --run '<RUN_ID>'
.venv/bin/python -m assets_generator.cli inspect \
  --store '<ARTIFACT_STORE>' --artifact '<RELEASE_ARTIFACT_ID>'
```

查询先验证 Artifact 内容摘要，再打印 manifest 和 JSON 内容（网格不打印二进制）。
`--run` 查询 Store 当前运行记录，可看到成功、失败及节点状态；run ID 来自构建结果、
输出 `run.json` 或 Store 的 `runs/` 索引。不是自动重试入口。
若构建失败，保留原 Store 和证据，修复输入/环境后选用新的输出目录重新执行。
已成功的候选、对齐结果和检查记录可复用；不要覆盖已发布包。

上述命令构成首版流程闭环，不证明代表性物体质量通过，也不关闭完整 Phase 6 的延期事项。

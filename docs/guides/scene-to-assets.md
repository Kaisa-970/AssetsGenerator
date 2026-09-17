# Phase 7 首版：提供 mask 的对象生成与显式场景装配

当前不是自动场景理解。用户提供物体 mask 和放置姿态；对象生成复用既有 Shape Backend。

## 从场景图生成对象资产

```json
{"schema_version":"1.0","image":"scene.png","objects":[
  {"object_id":"chair","mask":"chair-mask.png"},
  {"object_id":"table","mask":"table-mask.png"}
]}
```

```bash
assets-generator extract-scene --manifest objects.json --store '<STORE>' --output '<NEW_OUTPUT>' \
  --shape-backend trellis2 --trellis-python '<ENV>/bin/python' --trellis-repo '<REPO>' \
  --trellis-model '<LOCAL_SNAPSHOT>'
```

全部 mask 必须是与原图同尺寸的非空二值 mask，推理前统一校验。每个对象串行生成，
输出 objects/<id>/ 标准发布包；extraction.json 含每个 release 引用。失败时查询父 BuildRun
及节点 outputs.child_run；原子批次不会发布半成品。不得把生成的隐藏面称为观测重建。

## 显式放置为场景

按 [契约](../design/scene-to-assets.md) 编写 layout.json。world_pose.source_frame_id 必须为
`<asset_definition Artifact ID>/<asset.spatial.canonical_frame_id>`。组合资产的 canonical frame
可能本身包含 identity，因此必须读 asset.json，不能硬编码 asset_canonical。
世界使用 +Z up/+X forward；矩阵为列向量 canonical→world，平移是世界单位。
同单位才可放置；正统一缩放只是人工放置参数，不证明公制尺度。

```bash
assets-generator build-scene --manifest layout.json --store '<STORE>' --output '<NEW_SCENE>'
assets-generator inspect --store '<STORE>' --artifact '<NEW_SCENE>/scene-ref.json'
```

输出 scene.json、geometry/scene.glb、instances/、poses/、provenance/、run.json。
assets/<instance_id>/ 完整保留该实例原 AssetRelease 的 asset/release/files，不只是模型快照。
同一资产可放置多次，原 AssetDefinition 不改动。场景 GLB 已转换为 +Y up，不能再次按
canonical 轴解释。未验证遮挡、碰撞、物理关系、姿态质量或自动分割。

## SAM 自动 mask 候选

复用已有环境和本地 checkpoint，无需安装或下载 PyTorch：

```bash
assets-generator propose-instances --image scene.jpg --store '<STORE>' --output '<PROPOSALS>' \
  --sam-python '<SAM_ENV>/bin/python' --checkpoint '<LOCAL_SAM_CHECKPOINT>' \
  --points-per-side 16 --max-instances 20 --device cuda
```

查看 proposals.json 和 masks/ 后，明确选择需要的候选：

```bash
assets-generator select-instances --proposals '<PROPOSALS>/proposals-ref.json' \
  --proposal-id '<ID_1>' --proposal-id '<ID_2>' --reviewer '<NAME>' \
  --store '<STORE>' --output '<SELECTION>'
assets-generator extract-scene --manifest '<SELECTION>/objects.json' ...
```

选择顺序决定 object_001、object_002。不要把 SAM 分数解释为类别置信度，也不要自动将
全部候选送入 TRELLIS；重叠、部件和背景候选需要人工排除。

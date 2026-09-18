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

也可先写不含矩阵的布局草稿，并在本机可视编辑器中放置。草稿只包含 frame、unit、
instance_id 和精确 release 引用：

```json
{"schema_version":"1.0","frame_id":"scene_world","unit":"relative_unit","instances":[
  {"instance_id":"chair_1","release":{"artifact_id":"sha256:..."}},
  {"instance_id":"table_1","release":{"artifact_id":"sha256:..."}}
]}
```

```bash
assets-generator review-scene-layout --manifest layout-draft.json \
  --store '<STORE>' --output '<NEW_SCENE>' --port 8765
```

浏览器中选择实例，编辑 XYZ、yaw/pitch/roll 和正统一缩放，填写检查人后发布。服务仅监听
loopback；发布通过既有 `build_scene` 构造场景，并将 `layout.json`、场景包、
`SceneLayoutReview@1.0`、review provenance 和父子运行记录原子写入 `<NEW_SCENE>`。
Review 记录自报检查人、时间、完整布局、布局摘要、SceneDefinition 和精确 scene run_id。
预览依赖联网加载固定版本 Three.js；这仍是人工布局，不是位姿估计或物理验证。

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

启动仅监听 loopback 的可视检查页，在原图上查看 mask 叠加、明确勾选并调整输出顺序：

```bash
assets-generator review-instances --proposals '<PROPOSALS>/proposals-ref.json' \
  --store '<STORE>' --output '<SELECTION>' --port 8765
```

浏览器打开命令输出的 `http://127.0.0.1:<PORT>/`，填写检查人后发布。也可继续使用
非交互命令明确选择需要的候选：

```bash
assets-generator select-instances --proposals '<PROPOSALS>/proposals-ref.json' \
  --proposal-id '<ID_1>' --proposal-id '<ID_2>' --reviewer '<NAME>' \
  --store '<STORE>' --output '<SELECTION>'
assets-generator extract-scene --manifest '<SELECTION>/objects.json' ...
```

选择顺序决定 object_001、object_002。不要把 SAM 分数解释为类别置信度，也不要自动将
全部候选送入 TRELLIS；重叠、部件和背景候选需要人工排除。

若 SAM 只提供背景和物体部件，可在检查页只选择一个背景候选，再启用“取反”。
彩色预览代表取反后的前景；确认覆盖目标物体且不包含其他物体后再发布。
取反按整张图的二值补集执行，不保证补集只含一个物体。发布仍输出一个 object，
持久化新 mask、派生候选集合和 user-source provenance，保留原候选不变；
原 SAM 的 IoU/stability 分数不沿用到取反结果。取反得到空 mask 时拒绝发布。
多个普通候选仍各自生成一个物体，不自动求并集。

检查页支持显式“仅保留最大连通区域”，可独立使用或在取反后使用；仅接受一个候选。
使用 8 邻域，面积相同时保留按行扫描最先出现的区域，不填孔或改变保留区域的像素。
预览和发布使用相同规则；派生证据记录规则版本、连接方式、取反顺序与移除像素数。
分离的有效部件也会被移除，因此须检查预览；默认不启用。
已发布的选择不会覆盖，重新检查时请使用新的输出目录，例如 `selection-clean`。

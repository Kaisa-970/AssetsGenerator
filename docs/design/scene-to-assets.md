# Scene to Assets 首版契约

Phase 7 首版复用既有资产生成与发布能力，接入**显式提供的实例 mask 和用户位姿**。
它验证场景到独立资产、独立资产到场景实例的工程流程，不宣称自动检测、自动实例分割、
自动物体位姿估计或真实场景尺度恢复已经实现。无需新模型或环境。

## 由人工 mask 生成独立资产

`scene_extraction.extract_scene_objects()` 接收 manifest 路径、Store、输出目录及单图
`ResolvedPlan`，按对象顺序调用现有 `build_image_asset()`。GPU 推理串行。

输入 JSON：

```json
{
  "schema_version": "1.0",
  "image": "scene.png",
  "objects": [
    {"object_id": "chair", "mask": "chair.png"},
    {"object_id": "table", "mask": "table.png"}
  ]
}
```

路径相对 manifest 所在目录解析，也接受绝对路径。object_id 唯一，限 ASCII 字母、
数字、下划线和连字符，首字符为字母或数字，最多 128 字符。mask 必须和整幅输入图像
同尺寸、值为 0/255 且含前景。全部 mask 在首次推理前验证。mask 可以重叠；Core
不会推断重叠对象的遮挡顺序，也不自动修剪或修改用户 mask。

Core 将图片、mask 持久化，并保存 `SceneExtractionRequest@1.0`
(`scene_extraction_request`)：引用内容身份，记录对象 ID、人工分割来源、参数与
ResolvedPlan 契约摘要，不以文件路径作为内容身份。模型绑定和实际版本以每个子
BuildRun 为准。

`SceneExtraction@1.0` (`scene_extraction`) 返回逐对象 AssetDefinition、AssetRelease、
mask、子 run_id 及包内目录，另关联 request 和父 BuildRun。输出来源为 generated：
单图生成可能推断不可见区域，不保证保留观测表面。每个资产独立 canonicalization；
不同资产同名的 canonical frame 不表示它们共用坐标空间。

输出目录包含 `request.json`、`extraction.json`、`extraction-ref.json`、`provenance.json`、
`run.json` 和 `objects/<object_id>/` 标准资产包。整个批次原子发布，不覆盖已有输出。
中途失败不留下部分发布目录；成功或失败的子 BuildRun 及已产生 Artifact 保留于 Store，
父 BuildRun 记录完成与失败节点。这里不实现自动断点续跑。

## 显式装配 SceneDefinition

`build_scene` 读取如下 manifest；具体参数与 CLI 见使用指南：

```json
{
  "schema_version": "1.0",
  "frame_id": "scene_world",
  "unit": "relative_unit",
  "instances": [
    {
      "instance_id": "chair_1",
      "release": {"artifact_id": "sha256:..."},
      "world_pose": {
        "source_frame_id": "sha256:<asset-definition-digest>/asset_canonical",
        "target_frame_id": "scene_world",
        "matrix": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
      }
    }
  ]
}
```

World 使用右手、+Z up、+X forward，位姿遵循列向量
`p_world = T_world_asset * p_asset`。矩阵有限且为正统一缩放、旋转和平移；不支持剪切、
反射和非均匀缩放。首版仅接受资产与世界单位一致，不因用户指定数值就声称完成米制标定。

`AssetDefinition` 保持可复用，不写入 instance_id 或 world_pose。`AssetInstance` 保存
精确资产与 release 引用、唯一实例 ID 及显式位姿；同一资产允许多次实例化。
`SceneDefinition` 引用实例集合，保留人工布局来源。显式提供的场景不等同于自动估计的场景。

`review-scene-layout` 接受省略 `world_pose` 的布局草稿；草稿实例只允许 `instance_id` 和
精确 `AssetRelease` 引用。loopback-only 前端加载各 release 的 GLB，用户逐实例编辑平移、
yaw/pitch/roll 和正统一缩放。服务端负责根据 AssetDefinition 构造带作用域的 source frame，
生成完整布局 manifest，并以现有 `build_scene` 作为唯一场景构造边界。浏览器不得指定输出路径、
frame 或 release。`SceneLayoutReview@1.0` 作为独立不可变证据记录完整布局及其摘要、精确
SceneDefinition、BuildRun、自报 reviewer 和时间；它不改变 SceneDefinition 的身份语义。

审查服务使用外层原子发布目录：完整 layout、SceneLayoutReview、review provenance、父 BuildRun
和子场景包同时出现。父运行引用精确的 `build_scene` 子运行；review 证据持久化或最终发布失败时
不留下最终目录，但父子运行记录仍可在 Artifact Store 中查询。

HTTP 服务只绑定 `127.0.0.1`，检查 Host、同源 Origin、随机会话 token 和请求体上限；GLB
只通过会话启动时建立的固定索引提供，不接受文件路径。Three.js 预览使用和发布相同的
canonical→glTF 基变换。

释放的资产 GLB 已从 canonical 轴通过 E 转换为 glTF 轴。场景导出必须使用
`E * T_world_asset * inverse(E)` 作用于已释放的 GLB；不能直接将 canonical 位姿
作用于 glTF 几何。场景保留实例边界和材质，变换/provenance 可回查，原资产不修改。

首版不宣称碰撞、遮挡、接触关系、物理参数或布局质量已验证。后续自动检测/实例分割/
位姿 Backend 应替换人工输入来源，仍遵守这些不可变资产与实例分离契约。

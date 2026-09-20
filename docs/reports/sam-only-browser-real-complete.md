# SAM-only 编辑器与远程 TRELLIS.2 浏览器全链 smoke

日期：2026-09-21。代码基线 `946e379`。本次完成真实浏览器创建运行、SAM
推理、嵌入式 mask 确认、远程 TRELLIS.2 作业、页面继续、发布与完成后恢复。
这关闭此配置的正常执行浏览器全链验收，不关闭整个 Milestone C。

## 操作与证据

复用现有 sugar / TRELLTS conda 环境、SAM checkpoint、TRELLIS.2 本地权重及
robot.png；没有下载依赖或模型。独立启动真实 shape HTTP 服务和 SAM-only
编辑器，使用服务实际公布的身份配置目录。SAM 与 TRELLIS.2 串行执行。

`frontend/smoke/embedded-review.cjs --remote-submit` 通过浏览器创建运行，
选择第一个 SAM proposal 并确认。reviewer 明确为
`Codex automated browser smoke (not user approval)`。它是自动化选择，
不是用户批准或选区质量结论。

服务端 `drain --max-jobs 1` 执行已提交的唯一作业，成功退出。
`frontend/smoke/remote-complete.cjs` 再通过浏览器选择原运行，点击恢复，
等待发布并下载所有展示输出，然后再次恢复，对比节点、回执和输出。

- 父运行：`dag_4cbcc49093964bf49d8a7691cc67345e`。
- 远程提交键：`dag_5762091fbf60f396f31fc2b8070e0c18a35cb07ac1c1cefb46485cfc746dfad5`。
- 全部 8 节点 succeeded，各一次 attempt；决定回执保持一致。
- 浏览器无 pageerror；发布 GLB 与 Release 可下载；再次恢复未改变节点状态、attempt、回执或输出。
- 最终 GLB 用项目环境的 trimesh 独立回读：46,600,248 bytes，1 个 geometry，
  1,285,164 个顶点，2,598,104 个面。结构可读不等于视觉质量验收。

外部证据：`<DATASET_ROOT>/sam-only-browser-real-complete-v1/` 下配置、
endpoint.json、service.log、editor.log、execute.json/log、browser-submitted.json/png、
browser-completed.json/png、validation.json，以及 service.sqlite 与 Store。
模型、数据库、生成资产和截图未提交到仓库。

## 输出边界

只读查询服务固定 shape_metadata 确认
`postprocess_mode=geometry_fallback_no_texture`。元数据 Blob 为
`sha256:e821cfb1c92e7597648404b772c34ed2d028c2ff28d2a9f22972554471c30f8d`。
当前 CUDA/CuMesh 后处理仍采用无纹理几何 fallback，不能声称完整纹理生成通过。
QA 节点成功代表报告生成成功，不代表物体质量通过人工验收。

本次没有模拟推理中的服务异常退出，也没有验证远程公网部署、ComfyUI 或真实双模型比较。
这几项仍需独立实现或验收。检查完毕关闭本次两个监听服务，既有编辑器不受影响。

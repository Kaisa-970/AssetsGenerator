# 双真实 Shape Backend 浏览器全链验收

日期：2026-09-21；产品与浏览器脚本基线 `17204a5`。
复用既有 TRELLIS.2、TripoSR 环境和权重，独立 Store、服务数据库与编辑器目录。
使用 `frontend/smoke/shape-compare.cjs`，没有修改产品或脚本来迁就真实运行。

## 已验证

浏览器加载比较模板，分别选择 first-service、second-service，编译后上传同一
prepared RGBA PNG，点击启动。页面显示的上传 Artifact ID 与固定运行输入一致，
并与原 RGBA Artifact 身份一致。两个 shape 节点拥有不同服务身份和提交键。
服务端真实执行器按 TRELLIS.2、TripoSR 顺序执行，均正常退出；GPU 不并发。

两个作业成功后，重新打开浏览器选择原运行并显式恢复。运行
`dag_f501ac3e241e4d3688f791fee85ab0b8` 的十个节点成功，各一次 attempt。
两个 GLB/Release 均存在，所有展示输出可读；GLB 文件头及长度检查通过。
再次通过页面恢复，完整节点状态和输出引用不变，无浏览器 pageerror。

独立来源检查通过：两个 shape provenance 分别指向各自节点、父运行和共同输入，
没有 provenance ID 碰撞；资产定义和 release 分别独立，assembly 来源准确。
父运行完整 Artifact 证据闭包通过，trimesh 回读结果：

| Backend | GLB 字节数 | 顶点 | 三角面 |
| --- | ---: | ---: | ---: |
| TRELLIS.2 | 46,600,248 | 1,285,164 | 2,598,104 |
| TripoSR | 1,689,948 | 42,257 | 84,383 |

主验收及独立来源脚本均退出码 0。临时编辑器、两个 HTTP 服务和模型执行器已关闭。

## 证据与边界

仓库外 `<DATASET_ROOT>/dual-shape-browser-real-v1/` 保存 `verify.py`、`verify.log`、
`browser-config.json`、`browser-submitted.json`、`browser-completed.json`、截图、
`completed.json`、`validation.json`、`verify_provenance.py`、
`provenance-validation.json`、执行器日志及运行 Store/数据库。
主脚本仅用于新目录，已有 Store 时拒绝重复执行。

本次验证真实浏览器配置、上传、启动、恢复及输出下载；模型执行仍由服务端显式命令
触发，页面轮询不会自动派发。没有在本轮重跑 SAM 或作出人工 mask 决定，输入为已处理
RGBA；原 SAM/选择历史保留在原 Store，不声称上传会迁移整条来源链。
本轮未重启或杀死 HTTP 服务，不覆盖推理中断，也不是视觉质量或速度排名。
未重复完整代码回归；本提交仅同步验收文档，`git diff --check` 通过。

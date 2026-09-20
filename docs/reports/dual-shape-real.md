# 同一 RGBA 的双真实 Shape Backend 验收

日期：2026-09-21；产品代码基线 `122e115`（运行中仅更新设计状态文档）。
使用 `examples/remote-shape-compare.yaml`，两个真实 HTTP 服务分别绑定既有
TRELLIS.2 与 TripoSR profile。复用本地资源及 TripoSR frame validation，未下载权重。

## 已验证

运行 `dag_cc036bba4dfa43dab775f5cc8e9f35c3` 成功。两个 shape 节点消费同一精确
RGBA Artifact `sha256:b930142cc3776ca371db0e7e40c99f591e2fe708ac92e4506c635dd8a56ec6eb`。
两个请求均登记后，执行器按 TRELLIS.2、TripoSR 顺序串行执行；第一个分支先独立发布，
第二个随后发布。十个节点各一次 attempt。

| Backend | GLB 字节数 | 回读顶点 | 回读三角面 |
| --- | ---: | ---: | ---: |
| TRELLIS.2 | 46,600,248 | 1,285,164 | 2,598,104 |
| TripoSR | 1,689,948 | 42,257 | 84,383 |

父运行 Artifact 完整证据闭包通过校验。重新创建 DagEngine 并恢复已完成运行，
所有节点记录保持不变。独立来源检查确认两个服务身份与提交键不同，shape provenance
记录绑定各自节点实例、父运行与共同输入，没有 provenance ID 碰撞；两个资产定义、
release 引用不同，各发布的 assembly provenance 指向对应资产与节点。

主脚本和独立来源脚本均退出码 0；两次真实执行器均成功退出，临时 HTTP 服务已关闭。

## 证据与边界

仓库外证据位于 `<DATASET_ROOT>/dual-shape-real-v1/`：`verify.py`、`verify.log`、
`verify_provenance.py`、`provenance-validation.json`、`profiles.json`、`input.json`、
`submitted.json`、`after-first.json`、`after-second.json`、`completed.json`、
`validation.json`、两份执行器日志与服务数据库，以及 Store/运行目录。
主脚本拒绝向已有 Store 重跑，防止产生重复模型调用。

这是 API 脚本验收，不是双模型浏览器操作验收，也未重启 HTTP 服务。
RGBA 从前一项真实 SAM/选择运行的不可变 Blob 边界导入，字节和 Artifact 身份一致；
原始人工选择与 SAM 的完整运行证据仍保留在原 Store，不声称自动迁移其整条历史链。

本报告证明同类节点可绑定两个不同真实模型并分别发布，不是质量、速度或资源消耗
排名。两者参数语义不同，不从通用 seed 推断 TripoSR 具备随机采样控制。
GLB 可回读不等于纹理质量或仿真可用性验收。

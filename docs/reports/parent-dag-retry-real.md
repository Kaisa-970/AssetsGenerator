# 真实父 DAG 故障重试与发布验收

日期：2026-09-21；产品代码基线 `cb26c36`。复用既有 SAM、TRELLIS.2 环境和权重，
独立 Store、服务数据库与工作目录；GPU 串行，没有下载或修改模型资源。

## 事实

真实 SAM → 自动化选择确认 → RGBA → HTTP TRELLIS.2 → canonical → QA → assemble
→ publish 运行 `dag_d71502d29c7943519072e1f457ef89f8` 最终成功。
人工节点 reviewer 明确为 `Codex automated retry audit (not user approval)`，不是用户批准。

第一次生成在真实 runner 启动 5 秒后，仅 SIGKILL CLI 执行器。原进程组仍 alive 时，
显式放弃请求被拒绝；父运行恢复仍为 running，shape 只有一次 attempt，没有重新派发。
原组自然退出后，显式放弃未发布结果，父 shape 记录 `SERVICE_RESULT_ABANDONED`。
重新创建父执行服务对象，再显式 retry shape，使用新作业键与相同 RGBA 输入引用。
第二次真实模型作业成功，父运行发布完成。

独立回读校验确认：

- SAM、选择、RGBA 节点完整记录及人工回执与重试前完全一致。
- shape 两次 attempt，其余七个节点各一次；第一次失败记录原样保留。
- 父 BuildRun Artifact 的完整证据闭包通过校验；五个公开输出均可读取。
- 从精确 publish.glb Artifact 读取的 GLB 为 46,600,248 字节；trimesh 回读
  1,285,164 顶点、2,598,104 三角面。
- 主验收及独立验证脚本退出码均为 0，本次执行器、runner 与临时 HTTP 服务已退出。

## 证据与边界

仓库外目录 `<DATASET_ROOT>/parent-dag-retry-real-v1/` 保留 `verify.py`、`verify.log`、
`submitted.json`、`interrupted.json`、`parent-while-alive.json`、`exit-observation.json`、
`failed.json`、`retried.json`、`completed.json`、`validation.json`、
`verify_completed.py`、`independent-validation.json`、两个 executor 日志、Store 和数据库。
主脚本仅允许新目录首次执行，不可向已有数据库盲目重跑。独立验证初次寻找普通 GLB
文件未找到；改为读取发布 Artifact 指向的 Blob 后通过，没有修改运行或资产证据。

这是 HTTP 服务与 Core/NodeEditorExecution API 脚本验收，不是浏览器重试操作验收。
中断发生在 runner 加载/执行窗口，没有采样进度证据，不能称 GPU sampling 中断。
本次只重建父执行服务对象，没有杀死或重启 HTTP 服务进程；首次孤儿进程的临时输出
没有被采用为成功结果。GLB 可回读不构成几何、纹理或代表性物体质量验收。

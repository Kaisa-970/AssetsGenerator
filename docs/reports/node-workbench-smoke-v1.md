# 固定节点工作台：真实操作与恢复 smoke

日期：2026-09-18。范围：`photo_object_asset@1` 首个可运行切片。
代码位于独立 `feat/node-workbench-v1` worktree，未合并主分支。

## 事实

在现有独立 conda 环境及已有权重上，从 Chromium 页面完成：

1. 上传用户机器人 RGB 图片（561 × 688）。
2. SAM 以 `points_per_side=8`、`max_instances=10` 产生 10 个候选。
3. 选择背景候选，取反并保留最大八邻域连通区域；服务端保存预览。
4. 等待人工操作时对工作台服务执行 SIGKILL，随后用相同配置、Store 和工作台目录重启。
5. 核对恢复前后候选、草稿和最终 mask 完全一致。SAM 仍只有一个 attempt，子运行 ID 未变。
6. 由自动化 smoke 操作者显式确认（检查人标记 `Codex automated smoke (not user approval)`），
   不将其记作用户本人批准。
7. TRELLIS.2 以 seed 42、pipeline_type 512 完成生成、Core 后处理和标准发布。
8. 页面点击「查看模型」，Chromium 的 `model-viewer.loaded` 为 true，无 JavaScript 错误。

SAM 和 TRELLIS 的已登记 Worker 均以 exit code 0 结束，进程组探测为空。
本次没有重新下载模型或 PyTorch；DINO 使用既有 HF `snapshots/main` 指向 ModelScope 的
明确缓存引用，不扫描或猜测最新 revision。

## 身份与仓库外证据

`<DATASET_ROOT>` 指本次独立 `workbench-smoke-20260918` 目录，不提交其图片、权重或产物。

| 对象 | 身份 |
| --- | --- |
| 父运行 | `run_605f4bb9e104439f8855d2f23660191a` |
| SAM 子运行 | `run_627405a9f65544b78cd30a9fd5ffc3ee` |
| 选择子运行 | `run_ee4f9e13ee174522bcd12ec191cdb1ad` |
| 生成子运行 | `run_665acc2d56e4449a8db531ebdd704df8` |
| 原图 Artifact | `sha256:02d2014afc6d98a0f1835fa9ac57a8a57cb86324314f159bfe9e658e32f1647c` |
| 最终 mask | `sha256:70ceb9a0fc222f93395030f69976642168c31741458ddac7cf18906644a9c79f` |
| GLB | `sha256:5a81e298d48a4c0d7034a58d6b5b9c839a3676fb6138dcfbdc196266b90ac004` |
| AssetRelease | `sha256:f9a71eb9b47655afabafee09b730f4e28e312aea54aff8e42239aeca5bb8ad8e` |

证据文件：`<DATASET_ROOT>/before-restart.json`、`after-restart.json`、`completed.json`、
`run-evidence.json`、`workbench-real-preview.png`、`real-result.png`。
交付目录位于 `<DATASET_ROOT>/workbench/executions/<PARENT_RUN>/<GENERATE_RUN>/release/`。
测试服务在验证后关闭。

## 验证与观察

- 稳定实现版本的全量测试：675 passed。随后新增跨运行孤儿进程和损坏待办回归，5 项通过；
  后续没有重复声称运行过完整 680 项测试。
- Ruff lint、format、mypy 和 sdist/wheel 构建通过。
- Linux 普通子进程覆盖授权前 EOF、父服务消失、PID 复用、残留进程组及启动提交失败。
  新回归覆盖同工作台其他运行仍有存活/未知孤儿进程时阻止新计算。
- Fake Backend 的 HTTP 测试和 Chromium 点击测试通过；它们不替代上面的真实模型证据。
- 第一次并行开发期间的全量检查出现 4 项前端 stub 与页面版本不同步失败；修正后的前端
  回归通过，之后稳定版本全量 675 项通过。

QA：GLB 可加载、非空有限网格、Blob digest、空间契约和必需 provenance 为 pass；
metric_scale、deterministic_forward 为 warn；collision、render_back 为 skipped。
模型可在页面显示只证明交付和查看链路，不证明代表性质量验收。

## 未完成范围

完整设计 v1 尚未关闭：跨运行显式复用 mask 决定、从旧配置克隆、整包下载、子运行节点与
完整日志界面、完整的 scale/source/缺失检查摘要均未接入。自由连线不属于本切片。
TripoSR profile 已有配置与单元测试，本报告未执行工作台中的真实 TripoSR smoke。

真实验收覆盖人工等待时强制结束服务；推理中的父服务消失由普通 Linux 子进程测试覆盖，
没有对真实 GPU 推理实施 kill 测试，也不声称 GPU 内部断点恢复或断电耐久性实测。

操作说明见 [工作台指南](../guides/node-workbench.md)。

## 后续审查说明

该 smoke 证明指定图片的成功流程及人工等待恢复，不能据此声明所有恢复窗口已收口。
后续审查发现并补测：旧孤儿进程退出后重试需要刷新并持久化探测；父 stage 为 failed、
子运行已经成功时应在完整证据校验后首次恢复成功，避免重复推理。
这些修复的当前测试结果须单独报告，不计入上述历史 675/5 项测试数字。

本次未系统审计真实 Backend 是否有脱离进程组的辅助进程；普通子进程测试仅覆盖约定的
同组行为。检查人字段为自报身份，不提供认证或质量结论。
文档已保存在 Git 分支的提交中，正常删除 worktree 不会删除分支提交；当前尚未合并主分支。

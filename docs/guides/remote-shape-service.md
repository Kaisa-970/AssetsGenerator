# 本机远程 shape 服务实验入口

当前供开发验收使用，尚未完成真实 GPU 服务验收，也未接编辑器服务目录。复用已有 TripoSR/TRELLIS2 环境和模型，不安装 PyTorch 或下载权重。

配置 JSON 只包含 `profiles`，每个 profile 的字段沿用 workbench shape 配置（backend/python/repo/model，以及可选模型参数）；不包含 `sam`。所有资源路径指向现有本地资源，TripoSR 仍要求 frame_validation。

在 worktree 中显式设置 `PYTHONPATH=src`，使用项目 Python：

```bash
PYTHONPATH=src python -m assets_generator.remote_shape_cli serve \
  --config <CONFIG_JSON> --profile <PROFILE_NAME> --service-id shape-local \
  --database <RUN_ROOT>/service.sqlite --workspace <RUN_ROOT>/workspace --port 8770
```

启动会核验模型、代码和环境摘要，向 stderr 输出进度；stdout 打印 endpoint、service_id、backend_digest。客户端通过受信 RemoteShapeAdapter 绑定这些值。HTTP POST 只登记 queued，不会自动执行模型。Ctrl+C 关闭监听，不取消已在其他进程执行的作业。

另一终端使用相同配置、service-id、数据库和 workspace，将动作改为 `inspect --job <SUBMISSION_KEY>` 查询，或 `execute --job <SUBMISSION_KEY>` 显式执行一个 queued 作业。成功/失败输出 JSON；execute 对失败返回非零，重复执行不会重跑终态或 running 作业。

`observe --job <SUBMISSION_KEY>` 重新探测原进程身份。只有确认进程组退出才释放同数据库占用，不自动改写原作业状态或补跑。缺失身份仍阻塞。每个命令当前都会重新核验 profile，模型较大时启动较慢。

同一数据库只允许一个未确认退出的模型进程；不同数据库之间尚无 GPU 互斥。当前仅监听 127.0.0.1，无公网认证，不应通过反向代理开放公网。Store、数据库、模型、输入和输出均放仓库外。

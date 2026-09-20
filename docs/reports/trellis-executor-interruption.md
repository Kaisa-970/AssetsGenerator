# TRELLIS.2 执行器中断与孤儿进程门控验收

日期：2026-09-21，代码基线 `4fc50a8`。使用独立服务数据库、既有 TRELLIS.2
环境和权重，以及上一轮浏览器验收的 RGBA Blob。未重跑 SAM、未下载资源。

外部可复现脚本与证据位于 `<DATASET_ROOT>/trellis-executor-interruption-v1/`：
verify.py、verify.log、executor-pid.json、executor.log、interrupted.json、verified.json、
shape.json、service.sqlite 和 workspace。脚本通过真实 profile 核验生成服务身份，
从此前只读数据库导入输入，再启动独立 CLI execute；没有改写此前运行。

## 已验证

- 确认已登记 launcher 的子进程命令为独立环境中的真实 trellis2_runner.py，
  等待 5 秒后只 SIGKILL 本次 CLI 执行器，退出码为 -9。
- 重新打开服务数据库，原进程组仍 alive，重交原请求仍返回 running。
- 此时 abandon-exited 被拒绝；另一个测试请求可登记为 queued，但执行被拒绝，
  handler 未调用，没有重复启动模型。
- 原模型自然退出后，观察到进程组为空；原作业仍 running，原 WorkerExecution
  字节不变，exit_code 仍为 null。系统没有从临时输出重建或冒认成功结果。
- 显式 abandon-exited 后原作业 failed，错误为 SERVICE_RESULT_ABANDONED。
  第二个请求仍 queued，仅用作门控证据，没有执行。
- 验收脚本退出码 0；本次真实 runner 已退出，没有留下模型进程或 HTTP 监听。

## 边界

中断发生在真实 runner 启动后的模型加载/执行窗口；没有采样进度信号证明当时
已进入 GPU sampling，不能称为采样阶段故障验收。此处重开的是执行侧数据库，
没有重启 HTTP 服务，也没有涉及父 DAG 的浏览器重试或后续成功发布。

丢失执行器时不自动采用 runner 临时文件：缺少耐久的零退出码及服务成功结果，
必须保留不确定状态。已经退出不代表执行成功；显式放弃是放弃未发布结果，
不是取消仍在运行的模型。完整父 DAG 故障后重试链仍需独立验收。

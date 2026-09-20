# HTTP 监听进程 SIGKILL 回归

日期：2026-09-21。新增 `tests/test_remote_http_process_crash.py`；未修改产品实现。
既有 restart 测试通过正常 shutdown 重开服务，本回归实际启动独立 Python HTTP
监听进程，在独立执行连接已经领取作业、handler 尚未返回时 SIGKILL 监听进程。

验证内容：

- HTTP 离线后，父 B 节点保持 running，记录 remote_transport_unknown，原绑定及
  唯一 attempt 保留；显式 retry 被拒绝，服务数据库中作业仍 running。
- 相同端口、数据库、服务身份启动新监听进程，父管线继续认领原作业。
- 释放独立 CPU handler 后，原请求成功；菱形 A/C/D 各执行一次，B 一个 attempt，
  服务只登记一个作业，handler 仅调用一次。
- 再杀死监听进程，已完成父运行仍可从本地固定证据恢复，完整节点记录保持不变。

相关回归合跑：新增进程崩溃、编辑器远程 HTTP 发布/放弃重试、耐久服务菱形及
HTTP 重启测试，共 **5 passed in 12.00s**。Ruff lint/format、git diff --check 通过。
开发测试最初误用 Store context manager 及遗漏 retry revision，已修正测试；这些不是
产品恢复故障。执行连接使用独立 Store，并在 finally 释放等待、清理子进程。

本回归没有 GPU 模型；独立执行器是测试进程中的 CPU handler 线程，不宣称模型
进程或 GPU sampling 中断验收。真实 HTTP 监听服务在模型执行期间异常退出仍需
独立实测；此测试为该实测提供可重复的状态与幂等判据。

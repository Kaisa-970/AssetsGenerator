# 双服务比较管线的 CPU 浏览器验收

日期：2026-09-21。产品代码基线 `b48635f`，新增可复用
`frontend/smoke/shape-compare.cjs`，没有修改产品实现。

实际编辑器、两个本机 HTTP 服务与 CPU shape 测试 Backend 完成浏览器操作：
加载比较模板 → 分别选择两个具名 Backend → 编译 → 上传 RGBA → 启动。
两个远程节点引用同一上传 Artifact，服务身份、提交键独立。服务端串行执行后，
浏览器显式恢复，十个节点全部成功且各一次 attempt；两个分支的 GLB、Release
均可读，GLB 文件头与长度正确。再次恢复完整节点状态和输出引用不变。
浏览器没有 pageerror，验收脚本退出码 0，测试服务已关闭。

运行 ID：`dag_cbd1ed7bf0c74a60aeac3b3d9b2947e1`。证据位于仓库外
`<DATASET_ROOT>/shape-compare-browser-cpu-v1/`，包含启动脚本、配置、Store、
`browser-submitted.json`、`browser-completed.json`、截图与 `verify-third.log`。

前两次脚本在上传响应读取时遇到 Playwright `Network.getResponseBody` 缓存错误，
均尚未创建运行。脚本改为读取页面实际显示的 Artifact ID，再与运行精确输入比较后通过。
保留前两次失败日志，不把它们描述为产品推理失败。语法检查、Prettier 与
`git diff --check` 通过；本轮未重复完整测试套件。

这次是 CPU 测试 Backend 浏览器验收，不代表两个真实模型的浏览器全链已验收。
脚本的 submit/complete 分阶段保留明确执行边界，模型执行仍由服务端显式命令触发。

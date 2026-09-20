# 多图浏览器上传、真实重建与重启恢复

日期：2026-09-21；产品代码基线 `cb26c36`。本轮只扩展 smoke 脚本与文档，
没有修改产品实现。使用既有 DA3-Base、DA3 环境和 sugar 环境的 Open3D，
独立 Store 与编辑器目录，沿用官方 SOH 的 `000.png`、`010.png`。GPU 串行。

## 已验证

通过真实浏览器加载多视图模板，上传两张 RGB 照片并组装观测包，点击启动。
运行 `dag_b460b1cc3c794187aae6285e44c3faa7` 的 geometry、reconstruction、release
均成功，各一次 attempt。公开输出全部可读，浏览器无脚本异常。

完成后保存父运行快照，向编辑器服务发送 SIGTERM，等待旧进程退出；使用相同
资源配置、目录和 Store 启动新服务进程。浏览器重新选择运行，点击“恢复 / 继续此运行”。
恢复保持 succeeded，节点完整记录、回执、固定计划、输入及输出引用不变。
浏览器只提交了一次 resume，没有重新创建运行或重放模型。

独立脚本验证父运行完整 Artifact 证据闭包，并逐字节比较原照片与上传 Artifact Blob。
观测包图片顺序与上传请求一致，精确观测引用与运行输入一致。发布 GLB 为
2,292,388 字节，trimesh 回读 70,740 顶点、96,610 三角面。
浏览器主链、重启恢复、独立验证均退出码 0；本次模型进程和测试 HTTP 服务已关闭。

## 复现与边界

`frontend/smoke/multi-view.cjs` 的配置现在接受 `images` 文件路径数组，或已有
`observations` Artifact ID；二选一。输入目录必须独立，启动命令见节点编辑器指南。
恢复复用 `frontend/smoke/cpu-image-recover.cjs` 的 capture/recover 模式；虽然文件名
保留 CPU 示例名称，本次使用的是实际多视图运行，恢复检查没有模型类型分支。

仓库外证据位于 `<DATASET_ROOT>/multiview-upload-real-v1/`：`config.json`、
`browser-config.json`、`uploaded.json`、`created.json`、`completed.json`、
`completed.png`、`before-restart.json`、`restored.json`、`validation.json`、
`verify.py`、`browser.log`、`recover-browser.log`、服务日志及 Store/运行目录。

这是固定双视图配置的功能验收，不是代表性质量 benchmark。服务停止发生在发布
完成后，不覆盖 DA3/Open3D 推理中的服务崩溃。没有验收隐藏表面补全、相机精度、
更多视角或任意模型组合；不将 GLB 可读视为质量达标。

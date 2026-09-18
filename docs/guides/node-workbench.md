# 固定流程节点工作台

首版模板是「照片 → SAM 候选 → 人工选择区域 → 单图生成与发布」。四个步骤以节点显示，
尚不支持自由连线、增删节点或多对象生成。Backend 由本地配置提供，浏览器不接收模型路径
或任意执行命令。相关契约见 [固定流程节点工作台](../design/node-workbench-v1.md)。

## 使用边界

当前为可运行的固定模板切片；跨运行复用决定、克隆已有配置、整包下载和子运行日志界面尚未接入。

工作台在独立 worktree 开发；未合并前，主 checkout 中的旧命令不会自动具备工作台入口。
沿用原有虚拟环境时，务必让 `PYTHONPATH` 指向 worktree 的 `src`，避免 editable install
仍导入主 checkout。无需重新安装 PyTorch 或下载已有模型。

本地服务绑定 `127.0.0.1`。服务数据目录保存创建回执、子运行目录及服务锁；Artifact Store
保存不可变计划、输入、人工决定和 BuildRun 快照。重启必须使用同一配置、Store 和服务目录。
两者都应位于支持文件及目录 fsync 的本地文件系统。

## 本地配置和启动

创建仓库外的 JSON 配置，用实际本地路径替换占位符：

```json
{
  "sam": {
    "python": "<SAM_ENV>/bin/python",
    "checkpoint": "<MODELS>/sam_vit_h.pth",
    "device": "cuda",
    "points_per_side": 8,
    "max_instances": 10
  },
  "profiles": {
    "trellis-local": {
      "backend": "trellis2",
      "python": "<TRELLIS_ENV>/bin/python",
      "repo": "<TRELLIS_REPO>",
      "model": "<HF_CACHE>/models--microsoft--TRELLIS.2-4B/snapshots/<REV>"
    }
  }
}
```

主模型目录必须已存在。TRELLIS 的外部 decoder 和 DINO 引用只查询现有 Hugging Face 缓存，
缺失时直接报告错误，不下载。启动会计算权重、源码和依赖内容身份，首次启动可能需要一些时间。

```bash
PYTHONPATH="<WORKTREE>/src" <MAIN_CHECKOUT>/.venv/bin/python -m assets_generator.cli workbench \
  --config "<RUN_ROOT>/workbench.json" \
  --store "<RUN_ROOT>/store" \
  --directory "<RUN_ROOT>/workbench" \
  --port 8765
```

可另配 TripoSR profile：`backend` 为 `triposr`，提供其 `python`、`repo`、本地 `model`，以及
已通过真实 frame 验证的 `frame_validation` 文件。可设置 `chunk_size`、`mc_resolution`、
`foreground_ratio`、`timeout_seconds`。通用 `seed` 和生成分辨率参数对 TripoSR 不生效。

## 操作流程

1. 打开服务打印的网址，上传一张 RGB 或灰度物体照片，选择已配置的模型。可以设置随机种子
   和生成分辨率；具体参数是否生效由 Backend 定义。
2. 等待 SAM 产生候选，选择**一个**区域。如果选中的是背景，勾选「取反」；需要去除分离细条时，
   勾选「仅保留最大连通区域」。这也可能移除分离的有效部件。
3. 点击「更新预览」。服务端先取反、再执行八邻域连通区域清理，保存最终 mask。
   彩色区域是实际生成区域；检查后填写检查人，点击「确认区域并生成」。
4. 等待生成和发布。完成后可下载 GLB、AssetDefinition、AssetRelease 和 QA，或点击「查看模型」。
   浏览器查看器需要访问外部 CDN，加载失败会显示错误；可下载 GLB 后本地查看。

执行完成与质量检查分开展示。没有检查记录时显示「未评估」；人工确认只说明区域选择，
不表示几何质量合格。DA3/TSDF 多视图流程与场景布局暂未接入此模板。

## 恢复和重试

等待人工选择时可以关闭服务。重启后从「恢复已有运行」选择原任务，候选、草稿和确认记录
由后端恢复，不依赖浏览器缓存，也不重新运行已成功的 SAM。

失败或中断后先查看运行详情。工作台核对已登记进程；旧 Backend 仍活动或状态无法核实时，
不允许再次推理。确认旧进程退出后，可以显式重试，保留旧 attempt 并创建新子运行。
人工确认后中断的重试沿用已保存决定，不要求重新选择区域。

服务只托管 Backend 进程，Core 后处理仍在服务中。Backend 计算完成但服务在发布前崩溃时，
不保证能接管临时结果；显式重试可能重新运行模型。发布文件或 Store 依赖缺失时会拒绝恢复，
不会把目录存在或模型退出码当作发布成功。

正常 Ctrl+C 会等待已开始的工作保存结果。首版没有取消推理按钮；需要强制结束服务时，
下次启动按中断恢复处理。不要同时启动两个服务写同一个工作台目录。

## 验证说明

Fake Backend 的完整 HTTP 测试验证上传、预览、确认、发布，以及等待时重启不重复 SAM。
Linux 普通子进程测试验证启动门控、父进程消失和残留进程组。它们都不替代真实 SAM/Shape
模型验收；真实运行结果应单独记录报告，不以页面可用推断模型质量。

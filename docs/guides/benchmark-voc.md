# PASCAL VOC 基线

Phase 3 使用 PASCAL VOC 2012 作为第一版公开数据源。它适合小规模回归，但本项目只把它作为输入分割和几何生成的基线，不把 VOC 的检测框当作精确实例分割真值。

下载地址：<http://host.robots.ox.ac.uk/pascal/VOC/voc2012/VOCtrainval_11-May-2012.tar>

下载并解压后，在仓库外执行：

```bash
python -m assets_generator.voc --root /data/VOCdevkit/VOC2012 --output /data/voc-manifest.json --limit 20
```

该转换器读取 `SegmentationObject` 的实例像素标注，生成同尺寸二值 PNG mask，并在 manifest 中记录来源和许可证。像素值 `255`（边界/忽略区域）会被排除。正式发布前仍需复核 PASCAL VOC 的原始许可条款。

Benchmark 数据、模型权重和生成资产不提交 Git。运行时应保存 manifest、输入文件 digest、硬件、seed、QA、耗时和失败分类。

## CPU 预览与受控续跑

```bash
.venv/bin/python -m assets_generator.benchmark \
  --manifest /home/ypkwsl/Data/Datasets/voc-instances-v2.json \
  --output /home/ypkwsl/Data/Datasets/voc-baseline-smoke --preview-only
```

打开输出目录中的 `review.html` 查看原图、实例 mask、GLB 链接、耗时、显存及 QA。
`review-summary.json` 从所有现有 report 重建，包含失败详情；不依赖旧的 summary.json。

执行时支持 `--resume --limit 3`，或重复使用 `--case-id ID` 指定样本。
`--limit` 限制选择的样本总数，不是新增运行数；原 manifest 索引不会因筛选改变。
续跑按输入 digest 和运行模式匹配已有 report，包括已记录失败，避免自动重复昂贵任务。
需要重试失败时选定该 ID，不传 `--resume`，并使用新的输出目录。
中断但没有完整 report 的样本在续跑时写入独立 retry 目录，保留旧数据。
这是同配置续跑；更换 Backend、模型或评测配置时必须使用新输出目录。

在输出目录创建空文件 `STOP` 可停止后续派发，当前样本仍会完成。
移除 STOP 后才能继续派发。`--preview-only` 不启动 GPU，也不需要 Backend 参数。

这些 VOC 图片可能有多个主体，自动分割无法指定实例目标；当前基线应使用
`--mode provided`。原图/mask 预览不等于 3D 视觉质量验收，后者仍需检查 GLB。

预览页支持 GLB 交互查看（旋转、缩放、平移），点击“查看模型”按需加载，切换时
卸载前一模型。须通过 HTTP 打开，例如：

```bash
.venv/bin/python -m http.server 8765 --bind 127.0.0.1 \
  --directory /home/ypkwsl/Data/Datasets/voc-baseline-smoke
```

访问 http://localhost:8765/review.html 。查看器优先使用输出目录的
`viewer-assets/model-viewer.min.js`，缺失时使用固定版本的 CDN（model-viewer 4.0.0）。
这只是交互检查，不构成固定相机的 render-back QA。

# PASCAL VOC 基线

Phase 3 使用 PASCAL VOC 2012 作为第一版公开数据源。它适合小规模回归，但本项目只把它作为输入分割和几何生成的基线，不把 VOC 的检测框当作精确实例分割真值。

下载地址：<http://host.robots.ox.ac.uk/pascal/VOC/voc2012/VOCtrainval_11-May-2012.tar>

下载并解压后，在仓库外执行：

```bash
python -m assets_generator.voc --root /data/VOCdevkit/VOC2012 --output /data/voc-manifest.json --limit 20
```

该转换器读取 `SegmentationObject` 的实例像素标注，生成同尺寸二值 PNG mask，并在 manifest 中记录来源和许可证。像素值 `255`（边界/忽略区域）会被排除。正式发布前仍需复核 PASCAL VOC 的原始许可条款。

Benchmark 数据、模型权重和生成资产不提交 Git。运行时应保存 manifest、输入文件 digest、硬件、seed、QA、耗时和失败分类。

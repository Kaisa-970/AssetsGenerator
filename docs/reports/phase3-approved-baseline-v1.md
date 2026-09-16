# Phase 3 已筛选输入基线：VOC / TRELLIS.2

报告日期：2026-09-16。评测范围：VOC2012 中经人工检查通过的 3 个刚性对象输入，均使用提供的实例 mask。

## 结论

3 个输入均完成 TRELLIS.2 生成和发布。飞机与自行车模型保留为当前开发回归样本；瓶子模型因视觉质量不足淘汰。人工评价针对生成模型的可用性，不等同于 Geometry QA 通过率。

## 固定配置与运行证据

- 输入清单：`<DATASET_ROOT>/voc-rigid-approved-v1.json`
- 输出目录：`<DATASET_ROOT>/voc-approved-baseline-v1/`
- 运行模式：`provided`，`seed=42`，`pipeline_type=512`
- Shape Backend：TRELLIS.2，运行在独立 TRELLIS 环境
- 硬件：NVIDIA GeForce RTX 5060 Laptop GPU，8151 MiB，驱动 582.05
- 每个用例均完成 `resolve_mask`、`prepare_observation`、`generate_shape`、`canonicalize`、`validate`、`assemble_asset`、`export` 和 `materialize_release`
- 三例的 GLB 加载、非空有限网格、Blob digest、空间契约和 mandatory provenance 检查均为 `pass`
- 三例发布 digest 校验均为 `true`

`<DATASET_ROOT>` 是运行机器上的数据集根目录，不是仓库相对路径。输入、模型、生成资产和本地证据文件不随报告提交。
原始输入清单和评价文件按原始字节归档在输出目录的 `input-manifest.json` 和 `asset-reviews.json`。
清单共包含 10 个已批准输入，本报告只覆盖前 3 个；其余 7 个没有本批运行记录。
以下 SHA-256 标识归档文件的原始字节；本地清单中的路径变化会改变文件摘要。

| 证据文件（相对输出目录） | SHA-256 |
|---|---|
| `input-manifest.json` | `033ef51d09397bd17a4ee861cd5f0bf7b4c0379b75c2bd88ae461d4f8e8f3bfe` |
| `asset-reviews.json` | `6d8f298da9db43a74eeb5504c07920619dafb1fee770a02d748d9f6e40bceb00` |

## 用例结果

| 用例 | 类别 | Shape Backend 耗时 | Geometry QA | 人工评价 |
|---|---|---:|---|---|
| `voc2012_2007_000256_instance_01` | aeroplane | 231.2 秒 | warn | keep |
| `voc2012_2007_000584_instance_01` | bicycle | 411.9 秒 | warn | keep |
| `voc2012_2007_000346_instance_01` | bottle | 211.6 秒 | warn | reject |

三例的 QA `warn` 均来自相对尺度和机械估计的 forward 方向；collision 与 render-back 在本阶段未请求或不适用，不计为失败。

## 人工评价

评价文件：`<DATASET_ROOT>/voc-approved-baseline-v1/asset-reviews.json`。评价页面为输出目录中的 `review.html`，页面支持直接加载 GLB、旋转缩放和导出评价 JSON。

保留样本：飞机、自行车。瓶子淘汰，当前观察表明该输入的生成结果未达到开发回归用途的视觉质量要求。评价没有记录分项分数或具体缺陷，因此不能据此推断失败原因，也不能将结论推广到瓶子类别整体。

## 后续使用

飞机和自行车可作为开发回归样本及评审页面演示样本。它们不构成代表性 benchmark，也不替代按类别、尺度、遮挡和视角分层的正式评测集。瓶子用例保留其运行报告作为负例证据，不纳入保留样本清单。


瓶子记录为人工视觉质量不合格、具体根因未确定。Phase 3 要求记录质量失败及其证据，不要求修复模型的所有失败样本；执行成功与人工质量评价应分别统计。

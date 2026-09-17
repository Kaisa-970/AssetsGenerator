# DTU 多视图 TSDF 参数试评 v1

日期：2026-09-17。范围：3 个静态对象 × 8 张图 × 3 档 TSDF 参数的受控小样本试评。
不将本轮当作代表性 BenchmarkDataset 或完整 Phase 6 质量验收。

## 数据与固定配置

输入来自 [Gaussian Surfels 数据镜像](https://huggingface.co/datasets/turandai/gaussian-surfels-dtu)，
revision `70ec15cd824556d17493e1c18e7e6c8b79f3ad94`。
[项目说明](https://github.com/turandai/gaussian_surfels)明确该数据是按 IDR 约定预处理的
[DTU MVS 数据](https://roboimagedata.compute.dtu.dk/?page_id=36)。
使用扫描 24（建筑模型）、65（浅色颅骨模型）、110（金属色雕像）；选择先于推理，
每例按图像名称排序均匀选 8 个索引。输入接触表可见建筑重复纹理、浅色表面、暗色高光与细长肢体，
但样本数和视角覆盖不足以代表这些类别整体。
mask 来自数据集配对文件，转灰度后以 >=128 二值化，不做形态学修补。
输入、下载 URL 和 SHA-256、选择规则、脚本、运行证据均放在
`<DATASET_ROOT>/dtu-tsdf-pilot-v1/`，不将数据或生成资产提交仓库。

DA3-Base 固定 snapshot `f4a6c9b3c95e41c82048423d3493a81ec3fa810e`，process_res=392；
不消费数据集相机和法线。每例真实运行一次 DA3，随后在同一次脚本进程中重用完全相同的
相机、深度、点云 Artifact，三档 reconstruction 的输入证据一致。
重用在 BuildRun 标为 cache_hit，并在参数记录专用 reuse_policy；不实现跨运行 resume。
每次 Open3D 执行仍校验独立环境内容身份。GPU 串行执行。

| 参数档 | voxel_size_ratio | sdf_trunc_ratio | depth_trunc_ratio |
|---|---:|---:|---:|
| fine | 0.005 | 0.02 | 3.0 |
| baseline | 0.01 | 0.04 | 3.0 |
| coarse | 0.02 | 0.08 | 3.0 |

比率相对有效深度中位数。voxel 与 sdf 同比改变，因此是参数 profile 对比，不能单独归因
给 voxel_size。无补洞、删组件或重拓扑。导出显式 preserve_mesh，保留顶点颜色。

## 指标与判断边界

记录运行成败、耗时、顶点/面数、边界边和非流形边数量、连通组件、最大组件面数占比、
watertight、GLB 体积及 Core QA。指标按最终 GLB 的原始拓扑计算，不隐式焊接顶点。
边界边不等于孔洞数量；组件少不等于更准确。无真值对齐，不报告 Chamfer；
Phase 5 registration gate 未实现，不报告 silhouette/LPIPS 等 render-back 指标。
人工评价按形状、缺面、背景残留和颜色记录 keep/improve/reject，初始均 pending。

## 结果

9/9 真实发布成功，GLB 顶点颜色保留，Core QA 全部 warn，所有网格均非 watertight，
非流形边计数均为 0。这不是质量通过：每例的三档都有明显缺失表面。

| 对象 | 档位 | 三角面 | 边界边 | 组件数 | 最大组件面占比 | 耗时/s |
|---|---|---:|---:|---:|---:|---:|
| scan24 | fine | 35334 | 2156 | 89 | 0.977 | 10.25 |
| scan24 | baseline | 7984 | 594 | 12 | 0.994 | 28.80 |
| scan24 | coarse | 1765 | 221 | 2 | 0.999 | 23.15 |
| scan65 | fine | 30814 | 2972 | 177 | 0.954 | 12.38 |
| scan65 | baseline | 5845 | 703 | 28 | 0.973 | 22.43 |
| scan65 | coarse | 1177 | 189 | 10 | 0.978 | 26.81 |
| scan110 | fine | 26774 | 2466 | 197 | 0.943 | 17.17 |
| scan110 | baseline | 4953 | 611 | 29 | 0.986 | 19.46 |
| scan110 | coarse | 1015 | 169 | 6 | 0.993 | 18.27 |

耗时列包含 Core、环境摘要与早期统计调用，未包含复用的 DA3 前端时间，不能作为纯 TSDF 性能排名。
DA3 每例一次总耗时分别为 56.18/59.21/13.09 秒，PyTorch allocated 峰值均约 1698.91 MiB。
未控制系统负载或缓存，不根据一次运行耗时推断对象难度。

首轮统计脚本调用 trimesh 图算法时发现 Core 没有 graph engine。实际 release 均已成功；
原始 `results.json`/`run.log` 保留该后处理异常，`metrics.py` 使用并查集重新计算，
最终状态和指标以 `results-reviewed.json` 为准。没有安装依赖或重跑推理。
`validate.py` 已检查 9 个 release：原始输入摘要、mask 二值性、同例输入证据一致、参数与环境
provenance、release 内每个文件与 Artifact Store 字节一致均通过。


## 初步视觉观察方法

仓库外脚本 `render.py` 从最终 GLB 输出四个固定正交方位（0/90/180/270 度、仰角 20 度）
的诊断图，以三角面平均顶点色、无光照绘制，不是照片级渲染或像素质量指标。
`index.html` 提供交互式 GLB 及人工评价导出，默认用户评价 pending。
分析者的诊断观察与用户正式 keep/improve/reject 分开记录。

建筑模型：fine 比 baseline 保留更多窗格颜色细节，coarse 明显块状化。三档都存在开放表面
和缺失屋顶/背面区域；这不是单纯调粗 voxel 可以解决的问题。这里不归因于某个模型，
因为缺少真值相机/深度对照，也未控制输入视角覆盖。

颅骨模型：细档保留更多局部凹凸与颜色，但脸部/边缘缺面仍明显；粗档细节损失，未解决完整性。
金属色雕像：三档都出现薄片状轮廓和细长部件缺失，粗档尤其简化。颅骨和雕像的默认朝向也不自然，
说明 `up_axis=-Y` 的约定不能代表对象重力方向。没有 camera/depth 真值对照时不把这些失败归因于反光。

## 参数建议与下一步

后续诊断及用户检查确认照片没有覆盖背面；本报告的整体完整性观察不能直接作为模型失败
判据。已观测表面与背面开口的区别见 [后续表面诊断](tsdf-surface-diagnosis-v1.md)。

- fine 可作为后续诊断起点：本批保留更多可见颜色/表面细节，面数约为 baseline 的 4.4–5.4 倍；
  不据此宣布通用生产默认值，也不直接修改 Backend 默认参数。
- baseline 保留作工程对照；coarse 仅适合快速轮廓预览，本批不适合作为最终资产质量方案。
- 三档都缺面，当前不应靠 completion 掩盖。优先针对一个对象做 8/16 视图和更高 DA3 分辨率对照，
  检查 mask 后有效深度覆盖，并明确 native up 的来源；随后再界定补全触发与来源证据。
- 正式人工 keep/improve/reject 仍 pending，使用 `index.html` 逐例旋转检查并导出评价。
  本轮诊断图是分析者观察，不冒充用户验收。

本轮只增加报告和索引，未改 Core 代码；无需重跑已通过的 337 项代码测试。
报告更新完成后执行 git diff --check；真实 GPU 与 CPU 执行、9 份发布证据校验如上。

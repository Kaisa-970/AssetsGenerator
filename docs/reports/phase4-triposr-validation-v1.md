# Phase 4 TripoSR GPU 验证与回归运行

报告日期：2026-09-16。范围：TripoSR 独立环境、真实 GPU 执行、native frame 和 Phase 3 两个保留输入的自动工程检查。

## 结论

TripoSR Backend 已在目标机器完成真实 GPU 验证。飞机和自行车两例均生成并发布成功，GLB 可独立加载，网格非空且有限，Artifact digest、空间契约和 mandatory provenance 均通过。两例自动 QA 均为 `warn`，原因与 TRELLIS.2 基线相同：资产使用相对尺度，forward 为机械估计。

本报告证明 Backend 在该环境和固定配置下可运行，不构成代表性质量排名。尚未记录人工视觉评价，因此不能据此判断 TripoSR 与 TRELLIS.2 的视觉质量优劣。

基于 Registry、`ResolvedPlan`、配置驱动绑定、第二 Backend 接入和同输入真实 GPU 回归均已完成，Phase 4 于 2026-09-16 按既定实现范围关闭。人工视觉比较保留为后续评审活动。

## 固定环境与身份

- GPU：NVIDIA GeForce RTX 5060 Laptop GPU，8151 MiB，驱动 582.05。
- PyTorch：`2.9.1+cu128`，Compute Capability `sm_120` 已包含在编译架构中。
- TripoSR revision：`107cefdc244c39106fa830359024f6a2f1c78871`，运行时工作区 clean。
- Backend source digest：`sha256:a75ed8bb1ce1ee5152223335d1d0dc880b211b56b4e85a31d9b1cd2ae63b4ed1`。
- 模型内容 digest：`sha256:f60430256bb8d8d3905cfa525ec83a3858a668964f8e96398ebd5a82c194252d`。
- Core source digest：`sha256:4a156d0ce6f3e7c4a3fc008b3aaa1f9a2e653287231eee61acc3bc37813a5d19`。
- 参数：`chunk_size=8192`、`mc_resolution=256`、`foreground_ratio=0.85`。
- 输入模式：provided mask；通用 `seed=42` 和 `pipeline_type=512` 已记录为对 TripoSR 推理不生效。
- 本地证据目录：`<DATASET_ROOT>/triposr-regression-v5/`。

模型权重、输入、生成资产、Artifact Store 和运行目录不提交仓库。

## Native Frame 验证

验证规则为 `triposr-marching-cubes-glb-roundtrip-v1`。工具在 TripoSR 独立环境中使用官方 `MarchingCubeHelper` 和 CUDA `torchmcubes` 生成三个位置与尺寸均不同的标记体，再由 Core 环境独立 reload GLB。

验证结果：

- 三个标记的 X/Y/Z 位置和符号保持；
- scene node transform 为 identity；
- 导出前后尺寸与中心在容差内一致；
- 有向体积保持为正，未发生反射；
- 因此冻结 native frame 为右手、`+Z` up、forward unknown、`relative_unit`。

证据：`<DATASET_ROOT>/triposr-environment-evidence/triposr-frame-validation.json`。该次验证使用 Python 3.10.14、PyTorch `2.9.1+cu128`、CUDA 12.8 和 `torchmcubes 0.1.0`。证据绑定 Backend source、Backend 环境、fixture 和 validator；evidence digest 为 `sha256:24f8fc561c489d5db1529c5023a14786d82b49caf9f12626ade1b5acc773cec7`，PyTorch 核心扩展 digest 为 `sha256:b1fc7e58206d478db8b55d41b5c4925b432e181f38c7bce11d62bf40de978090`，`torchmcubes` package digest 为 `sha256:b75a03be23b308281b172a8506b3ff5e5ef89fde3d91d98e703974f3b7ed212a`，其原生扩展 digest 为 `sha256:09ecae500c4ce04f0b28c907f8dd6f34f568e1a8748bd738de1fdb1b9b4fc911`。fixture digest 为 `sha256:0826d78c26a8376ef4701ba1afce9b7868f6c9d6815ef70ade53ae0e87a2918b`，validator digest 为 `sha256:cc643a9b28044729411cd6e589276652756ce6b46d3ada8c4869aef4e3c6aec9`，GLB digest 为 `sha256:76cb8c1e6074424764f16a238626e6c62683e41b99d66a4d48a4e96289b1c95e`。

## 回归运行结果

| 用例 | 类别 | 总耗时 | PyTorch CUDA allocated 峰值 | 顶点 | 面 | QA | 发布 digest |
|---|---|---:|---:|---:|---:|---|---|
| `voc2012_2007_000256_instance_01` | aeroplane | 43.4 秒 | 4237.9 MiB | 15,604 | 31,147 | warn | valid |
| `voc2012_2007_000584_instance_01` | bicycle | 43.0 秒 | 4238.0 MiB | 18,934 | 37,847 | warn | valid |

显存数值来自 PyTorch allocator，不代表整卡峰值。运行串行执行，没有并发模型进程。

## 与 TRELLIS.2 基线的可比范围

两组运行使用相同输入和 provided mask，并经过相同 canonicalization、GLTF2 export、Geometry QA、BuildRun 和 provenance 流程。TRELLIS.2 基线 Shape Backend 耗时分别为 231.2 秒和 411.9 秒；本次 TripoSR 总流程耗时分别为 43.4 秒和 43.0 秒。由于计时边界不同，前者是 Shape Backend 节点耗时，后者是完整单例流程耗时，这些数字只用于描述当前机器上的工程运行，不能直接计算模型速度比或推断跨硬件性能。

自动检查只能证明输出结构有效，不能评价主体完整性、外观相似度、纹理、背面合理性或仿真可用性。下一步应在 `<DATASET_ROOT>/triposr-regression-v5/review.html` 中完成人工评价，并与 Phase 3 TRELLIS.2 输出并排记录结论。

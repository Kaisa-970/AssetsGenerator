# DA3-Base Geometry Frontend GPU Smoke v1

日期：2026-09-17。范围：独立进程的 RGB-only geometry frontend 功能验证。

## 事实

在 RTX 5060 Laptop（8151 MiB）上，官方 SOH 两张图片经真实 DA3-Base 推理，
输出通过 Core 的 geometry_frontend 端口、CameraRecord、深度关联和跨输出空间校验。
未执行 reconstruction Backend，也未生成 triangle mesh / PBR / AssetRelease。

- 上游源码 revision：`3d835ec1a5802d64a8b8b15f817a1ab54809bfe4`，clean。
- 模型：`depth-anything/DA3-BASE`，revision `f4a6c9b3c95e41c82048423d3493a81ec3fa810e`。
- snapshot digest：`sha256:650685fe400f06f7e2c8a6a12b270a14a38e270bb6fa661081ce4de38a4ee6b7`。
- 原图：2 × 1208×680，推理尺寸 392×224，process_res=392。
- 输出：2 个 estimated camera、2 张原图尺寸 float TIFF、182544 个 PLY 点。
- 单位：relative_unit；world frame：da3_world。
- Worker/adapter 总耗时：8.964 秒；模型加载与推理：2.883 秒。
- PyTorch 峰值 allocated：899.40 MiB；reserved：1084 MiB。
  这些是进程 allocator 指标，不是整卡使用量，也不是最大可处理帧数的结论。
- 全局置信 40 分位阈值在此样例为 1.0；相同分数可能使实际保留比例超过 60%。

证据：`<DATASET_ROOT>/da3-smoke-v1/result.json`、`smoke.log`、`source.json`、
`store/`、`environment-before.txt`、`environment-after.txt`。

## 环境与限制

DA3 环境通过离线克隆现有 TRELLTS 创建；torch、torchvision、triton、全部 NVIDIA
包版本前后比对一致，没有重新下载 PyTorch。实测 PyTorch 为 2.9.1+cu128。
后续仅安装 DA3 API 必须的缺失包，未安装 xformers、gsplat 或 Web UI 全套依赖。

`pip check` 未通过上游完整依赖声明：缺少 open3d、opencv-python、pre-commit、xformers，
且克隆的 NumPy 2.2.6 不满足上游 numpy<2。当前 cv2 来自已有发行包；本次真实推理成功
仅验证所用 API 路径，不代表上游所有工具兼容。保留原环境依赖以避免替换已验证的 CUDA 栈，
后续扩展功能时需逐项解决。完整输出保存于 `<DATASET_ROOT>/da3-smoke-v1/pip-check.txt`。

输入是上游两视图场景，非代表性物体数据集。未量化几何精度、米制尺度、纹理或物体表面质量。
本报告证明固定配置的 frontend 可运行，不关闭完整 Phase 6。

## Core 验证

- 全量 pytest：278 passed。
- DA3 adapter/runner：25 项测试覆盖进程协议、身份、原图坐标恢复和空间校验。
- Ruff format/check、mypy（34 source files）、sdist/wheel build 通过。

## 下一步

准备同一物体的真实多视图输入，串行评测 4/8/16 帧的质量与资源开销；
独立确定网格融合与纹理 Backend，完成真实资产发布链路。

# DA3 Geometry Frontend

Phase 6 首个真实 geometry frontend 选择 Depth Anything 3 的 DA3-Base。
本文件定义接入范围；GPU 可运行性以验证报告为准。

## 执行边界

Core 使用 `DA3GeometryFrontend` 实现 `geometry_frontend@1`，通过
`LocalProcessWorker` 调用独立 Python 环境中的 `da3_runner.py`。
Core 不导入 PyTorch 或上游模型。源码、权重和运行结果均位于仓库外。
模型使用本地固定 snapshot，执行记录源码、模型内容和 runner 摘要。

首版为 RGB-only：输入保持 ObservationBundle 的 view 顺序，mask、已有 depth
和 provided camera 不作为模型条件；不得宣称支持 RGBD 条件推理或 CameraRegistration。
Registry 可注册该实现，多视图构建仍需单独提供真实 reconstruction Backend。

## 空间和输出

- 相机输出使用 `source=estimated`、pinhole，模型 w2c 逆为 `T_world_camera`。
- 共同 world frame 为 `da3_world`，相机 frame 为 `da3_camera_<index>`。
- 输出单位统一为 `relative_unit`；DA3-Base 不提供已校准的米制尺度。
- 深度为原图尺寸的 float TIFF，零表示无效；内参还原模型 resize/crop。
- 裁剪外区域不得伪造深度；变换参数、过滤规则必须记录在 metadata 中。
- 点云由相同深度、内参和外参反投影获得，使用 PLY。
- 禁止使用 demo GLB 的场景居中、轴变换代替显式空间契约。

此 Backend 不输出 triangle mesh、PBR 或完整资产；网格融合、纹理和 reconstruction
Backend 不属于本次接入。官方两图样例只适合功能 smoke，不代表物体重建质量 benchmark。

## 环境

沿用已有 CUDA 12.8 / PyTorch 2.9.1 构建离线克隆独立环境，禁止为安装 DA3
替换已有 TRELLIS 环境。只补缺失依赖，安装上游包时使用 `--no-deps`。

本机约定：源码 `<DA3_REPO>`，独立 Python `<DA3_ENV>/bin/python`，
权重 `<DA3_MODEL_SNAPSHOT>`，证据 `<DATASET_ROOT>/da3-smoke-v1`。
权重选择 `depth-anything/DA3-BASE`（Apache 2.0），不把该许可推广到整个模型系列。

参考：[官方仓库](https://github.com/ByteDance-Seed/Depth-Anything-3)、
[Base 模型卡](https://huggingface.co/depth-anything/DA3-BASE)。

# DA3 独立环境

接入范围见 [DA3 契约](../design/da3-backend.md)。独立环境不修改 Core 或已验证的 TRELLIS 环境。

## 复用 PyTorch

本机使用 `conda create --name DA3 --clone TRELLTS --offline`，保留已有
PyTorch 2.9.1+cu128、TorchVision 和 NVIDIA CUDA 包，不重新下载 PyTorch。
在克隆环境中固定这些包版本后，仅安装缺失的 `evo`、`e3nn`、
`moviepy==1.0.3`、`pillow_heif`、`pycolmap` 以及 `addict`、`editables` 和构建工具。
不要直接安装上游全部 requirements：它还包含本次不需要的 Web UI、Gaussian rendering 等依赖。
上游注意力实现支持 PyTorch SDPA，本次不需要安装 xformers 或 gsplat。

```bash
<DA3_ENV>/bin/python -m pip install --no-deps --no-build-isolation -e <DA3_REPO>
```

源码 revision 与本地模型 snapshot revision 必须固定；`DA3-BASE` 与上游 quick-start
默认的大模型不同。测试输入、Store、日志和生成结果均写入仓库外 `<DATASET_ROOT>`。

## Python 接入

```python
from pathlib import Path
from assets_generator.backends.da3 import DA3GeometryFrontend

frontend = DA3GeometryFrontend(
    python=Path("<DA3_ENV>/bin/python"),
    repo=Path("<DA3_REPO>"),
    model=Path("<DA3_MODEL_SNAPSHOT>"),
    process_res=392,
)
# observations 是 import-observations 得到的 ArtifactRef。
result = frontend.estimate(store, observations)
registry.register(
    name="da3_base",
    operator="geometry_frontend@1",
    backend_version="da3-base-v1",
    implementation=frontend,
)
```

实际运行至少提供两张同一静态场景图片。默认采用全局有效预测置信的 40 分位过滤，
这不是校准概率。点云使用确定性步长采样，目标上限约 20 万点，不含颜色或 PBR。
混合长宽比的中心裁剪边缘写零深度。所有推理串行执行。

必须区分前端 smoke、物体质量 benchmark 和完整 mesh release；前端成功不关闭完整 Phase 6。

此最小推理环境与上游完整依赖声明存在差异，`pip check` 尚未通过；具体缺项与真实 smoke 结果见 [验证报告](../reports/da3-frontend-smoke-v1.md)。

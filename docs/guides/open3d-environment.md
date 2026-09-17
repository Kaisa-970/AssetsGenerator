# Open3D 环境与接入

本机复用现有独立 `sugar` Conda 环境，已验证可导入 Open3D 0.19.0、trimesh 4.10.1、
NumPy 2.2.6、Pillow 12.3.0。未安装或替换 PyTorch；Core 环境不安装 Open3D。
其他机器只需提供包含这些库的独立 Python，TSDF runner 仅使用 CPU。

```python
from pathlib import Path
from assets_generator.backends.da3 import DA3GeometryFrontend
from assets_generator.backends.open3d_tsdf import Open3DReconstruction
from assets_generator.backend_registry import BackendRegistry, resolve_plan
from assets_generator.pipeline import load_multi_view_pipeline, load_default_operator_specs
from assets_generator.multi_view_workflow import build_multi_view_asset

registry = BackendRegistry()
registry.register(
    name="geometry_frontend", operator="geometry_frontend@1", backend_version="da3-base",
    implementation=DA3GeometryFrontend(
        Path("<DA3_ENV>/bin/python"), Path("<DA3_REPO>"), Path("<DA3_MODEL_SNAPSHOT>"),
    ),
)
registry.register(
    name="reconstruction", operator="reconstruction@1", backend_version="open3d-tsdf",
    implementation=Open3DReconstruction(Path("<OPEN3D_ENV>/bin/python")),
)
plan = resolve_plan(load_multi_view_pipeline(), registry, operator_specs=load_default_operator_specs())
result = build_multi_view_asset(
    observations=observations,  # 同一个 Store 中已导入的 ObservationBundle ArtifactRef
    store_path=Path("<ARTIFACT_STORE>"), output_path=Path("<RELEASE_DIR>"),
    resolved_plan=plan, export_appearance_mode="preserve_mesh",
)
```

使用静态对象的重叠视图；有物体 mask 时随 ObservationBundle 提供。
DA3 首版不消费 mask，但 Open3D 融合会使用 mask 排除背景。
TSDF 尺度规则和局限见 [Backend 契约](../design/open3d-backend.md)。

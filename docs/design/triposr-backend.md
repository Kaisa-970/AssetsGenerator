# TripoSR Shape Backend Contract

**状态**：Phase 4 第二个真实 Shape Backend 的接入契约。

## 1. 范围

TripoSR 注册名为 `triposr`，实现现有 `shape_generation@1`。它只负责把单张 RGBA 观测转换为一个 GLB Mesh，不修改 OperatorSpec、canonicalization、validation、AssetDefinition 或 export 契约。

Core 环境不得导入 PyTorch、CUDA、TripoSR 或 `torchmcubes`。适配器继续使用独立 Python 进程、JSON request/response、Artifact 路径和 `LocalProcessWorker` 超时语义。

## 2. Backend 配置

`TripoSRBackend` 至少接受：

```text
python: Path
repo: Path
model: str | Path = "stabilityai/TripoSR"
timeout_seconds: float = 900
chunk_size: int = 8192
mc_resolution: int = 256
foreground_ratio: float = 0.85
```

约束：timeout、chunk size 和 marching-cubes resolution 必须为正；foreground ratio 必须在 `(0, 1]`。模型专用参数属于 Backend 配置，不进入 `shape_generation@1` 的端口定义。

## 3. Runner 请求

适配器写入的 request JSON 使用绝对路径：

```json
{
  "repo": "/absolute/TripoSR",
  "model": "stabilityai/TripoSR",
  "input_image": "/absolute/input.png",
  "output_glb": "/absolute/native.glb",
  "seed": 42,
  "pipeline_type": "512",
  "chunk_size": 8192,
  "mc_resolution": 256,
  "foreground_ratio": 0.85
}
```

输入必须保持 RGBA alpha。Runner 不得对已有有效 alpha 的输入再次执行背景分割。

TripoSR 推理没有与现有 `seed`、TRELLIS `pipeline_type` 等价的控制项。适配器接受这些通用参数以满足协议，并在 response/provenance 中记录：

```text
seed_effective: false
pipeline_type_effective: false
```

不得伪造参数已经影响模型推理。

## 4. Runner 响应

成功响应至少包含：

```json
{
  "backend": "triposr",
  "backend_version": "workspace-<git revision>[-dirty]",
  "model": "stabilityai/TripoSR",
  "model_digest": "sha256:...",
  "seed": 42,
  "seed_effective": false,
  "pipeline_type": "512",
  "pipeline_type_effective": false,
  "chunk_size": 8192,
  "mc_resolution": 256,
  "foreground_ratio": 0.85,
  "vertex_count": 0,
  "face_count": 0,
  "peak_cuda_memory_mb": 0.0
}
```

模型 digest 必须基于实际本地 snapshot 文件内容。Git revision 和 dirty 状态只描述适配器源码，不能替代模型内容身份。

## 5. 输出语义

- 输出 Artifact：`triangle_mesh / glTF 2.0`。
- 单位：`relative_unit`。
- forward：`None / unknown`，不得根据单图猜测。
- 初始材质目标：可渲染 vertex-color GLB；GLB 内材质和颜色为视觉权威。
- Structured `PBRMaterial` 只描述可表达的因子或纹理引用，不得声称 vertex color 已转换成完整 PBR maps。

TripoSR 源模型空间预期为右手、`+Z` up，但在适配器声明 `BackendNativeFrame` 前，必须使用非对称轴标记 fixture 完成“导出 GLB -> 独立 reload”验证，确认导出没有附加坐标变换。验证结果写入测试和 Backend 元数据；不能仅依据 glTF 约定将 native frame 标为 `+Y`。

## 6. Registry 与 CLI

完成实现后，CLI registry 同时注册：

```text
trellis2 -> shape_generation@1
triposr  -> shape_generation@1
```

Pipeline 默认仍为 `trellis2`。只有显式传入 `--shape-backend triposr` 才切换。TripoSR 的 Python、repo、model、timeout 和模型专用参数使用独立 CLI 参数，不能复用或改写 TRELLIS 参数含义。

## 7. 验收

合并前必须满足：

1. 不导入模型依赖的单元测试覆盖请求、响应、Artifact identity、错误、超时和 registry 切换。
2. Runner 模块可在 Core 测试环境中被静态检查，但模型 import 只发生在 runner `main()` 内。
3. 使用小型 fixture 验证 GLB 可加载、非空、frame/unit 完整及材质可渲染。
4. 在独立 TripoSR 环境完成一个真实 GPU smoke；记录 CUDA/PyTorch、模型 digest、峰值显存和耗时。
5. 在 Phase 3 保留的飞机与自行车输入上各运行一次，通过同一 review 页面比较 TRELLIS.2 与 TripoSR。

环境安装和真实 GPU 结果属于后续验证证据。未完成真实运行前，只能声称 adapter 和契约测试通过，不能声称 TripoSR Backend 已可用。

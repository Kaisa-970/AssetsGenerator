# 添加自己的图生 Mesh 模型

服务发现协议支持一个服务声明多个 `capabilities[]`。每项能力必须引用版本化 Operator、传输协议、参数 schema、默认值和空间声明。当前可执行的已知能力是 `shape_generation@1`：输入一张 RGBA PNG，输出内嵌资源的 GLB、材质摘要和原生坐标声明。画布端不需要为每个符合协议的模型新增 Python Adapter。一个服务中的多个 shape capability 首版必须共享同一套输出坐标系和单位；如果不同能力输出不同 frame/unit，请拆成独立服务部署。

未知能力可以在检测页查看其名称、参数和空间声明，但当前不会被安装为可执行节点，也不能进入规范化、QA 或发布链。每个已知能力都可以单独安装，并生成独立的 Backend 身份；同一服务的其他能力不会混入该绑定。后续增加新的 Operator 契约后，才会开放对应能力的安装与执行。

## 画布使用者

1. 打开编辑器的“模型服务”，填写服务地址，点击“检测”。
2. 逐项检查模型能力、输入输出、参数、坐标系和单位；只有后端具有对应 Operator 契约和服务执行适配器的能力可以添加。检测页显示后端返回的安装资格；仅有端口契约或传输协议不受支持时，能力只能查看，并显示原因。
3. 从节点目录添加该模型，连接 RGBA 图片输入。
4. 配置模型参数并运行，预览输出 Mesh；需要正式资产时继续连接规范化、QA、组装和发布。

RGB 原图不能直接连接 RGBA 端口。如果有 mask，应通过“应用遮罩”节点生成 RGBA。服务声明不能创造新类型或绕过 OperatorSpec，错误类型仍由后端编译拒绝。

## 模型部署者：实现一个推理函数

在模型自己的 Python 环境中安装本项目及模型依赖，编写启动文件。不要把 CUDA 或模型代码装入编辑器环境。

```python
from pathlib import Path
from assets_generator.models import BackendNativeFrame
from assets_generator.shape_model_service import ShapeModelService

# 在这里导入和加载自己的模型。
# model = load_model(...)

def infer(rgba_path, parameters):
    # result = model.generate(str(rgba_path), steps=parameters["steps"])
    # return result.export(file_type="glb")
    raise NotImplementedError("替换成模型调用，返回 GLB bytes")

service = ShapeModelService(
    service_id="my-image-mesh",
    display_name="我的图生 Mesh 模型",
    deployment={
        "model_revision": "填写不可变模型版本或权重摘要",
        "code_revision": "填写部署代码的不可变版本",
        "environment_digest": "填写已验收环境摘要",
    },
    frame=BackendNativeFrame(
        "my-model-native", "right", "+Y", None, "unknown", "relative_unit"
    ),
    infer=infer,
    parameter_schema={
        "type": "object",
        "properties": {"steps": {"type": "integer", "minimum": 1, "maximum": 100}},
    },
    defaults={"steps": 30},
    directory=Path("/path/to/durable-service-data"),
    port=8780,
)
service.serve()
```

同一服务也可以暴露多个独立的 `shape_generation@1` 能力。每个键会成为一个
`capability_id`，参数 schema 和默认值可以不同；工作台检测后可分别添加，分别生成
Backend 身份。回调函数仍然是同一个 `infer`，可以依据参数或能力所需的默认配置选择模型：

```python
service = ShapeModelService(
    # 其他参数与上例相同
    capabilities={
        "mesh_fast": {
            "display_name": "快速网格",
            "parameter_schema": {
                "type": "object",
                "properties": {"steps": {"type": "integer", "minimum": 1}},
            },
            "defaults": {"steps": 12},
        },
        "mesh_quality": {
            "display_name": "高质量网格",
            "parameter_schema": {
                "type": "object",
                "properties": {"steps": {"type": "integer", "minimum": 1}},
            },
            "defaults": {"steps": 48},
        },
    },
)
```

首版要求这些能力共享服务级输出 `frame_id`、`up_axis` 和 `unit`，因为回调输出的
GLB provenance 使用同一套坐标声明。不同坐标输出请拆成两个服务地址；未知 Operator
仍只能检测和查看，不能直接进入标准发布链。

`infer` 收到经过验证的 RGBA 文件路径和按 schema 规范化的参数，必须同步返回自包含 GLB 字节。纹理应内嵌，保留顶点颜色或已有材质；不要返回外部纹理 URL。原生坐标和单位必须如实填写，不能假定所有模型都输出 Y-up 或米。默认材质摘要不替换 GLB 内的真实外观。

包装工具提供服务发现、参数声明、上传验证、SQLite 作业登记、按键查询、结果摘要与自动串行执行。首次添加时不执行推理。部署身份必须由部署者根据实际权重摘要、代码 revision、Python/依赖环境摘要、参数定义、默认值和坐标声明共同生成，并在部署内容变化时更新 `backend_digest`。编辑器只能核对服务前后声明和摘要是否一致，不能替部署者验证摘要是否真实，也无法发现“实际模型已更换但身份声明未更新”的情况。更换部署请使用新数据目录，并在编辑器重新检测和添加。

服务只监听 `127.0.0.1`。跨机器可用 SSH 隧道，例如在编辑器机器执行：

```bash
ssh -N -L 8780:127.0.0.1:8780 user@model-server
```

然后在编辑器填写 `http://127.0.0.1:8780`。首版不提供公网认证或自动部署。

## 不下载模型的接入演练

仓库提供 CPU 盒子示例，**它不是图生 3D 模型，不证明生成质量**：

```bash
PYTHONPATH=src python examples/shape_model_service_cpu.py \
  --directory /tmp/shape-model-example \
  --port 8780
```

检测并添加 `http://127.0.0.1:8780`。示例取输入图片中心颜色，生成可调宽度的盒子，验证发现、动态参数、实际请求和 GLB 预览闭环。

## 重启与边界

- 成功任务按原提交键找回结果，不重复调用模型。
- 已知参数/图片/结果校验错误会记为失败，后续作业可继续。
- 推理中断后遗留 `running` 作业会阻塞后续领取，包装工具不猜测成功，也不自动重新推理。首版需要部署管理员查明状态后处理，不承诺自动恢复模型内部计算。
- 回调在独立模型服务进程中同步执行，不得留下脱离进程的后台推理；这种服务需要更强的进程所有权适配，不属于此简易包装接口。
- 不支持任意第三方 REST/Gradio 地址的自动理解。现有服务可在旁边运行这个包装层，将回调接到现有模型函数；若调用另一套异步服务，需要单独实现它的提交不确定性恢复。

## 复用已有 TripoSR 部署

已有 TripoSR profile 可直接通过 `assets_generator.remote_shape_cli serve` 提供服务发现；
编辑器填写该监听地址即可添加。该入口沿用 `ShapeServiceHandler / ServiceProcessWorker`，
保留模型前后身份核验和持久化进程登记，不使用简易 callback 包装子进程。

发现页声明 `triposr_glb_native / +Z / relative_unit`。seed 和 pipeline_type 为旧请求
协议保留的固定值，对 TripoSR 推理不生效；chunk_size、mc_resolution 等仍在部署 profile 配置。
部署时同时启动 `serve` 和 `work` 后，任务会自动串行执行；也保留显式 `execute` / `drain`。只启动 HTTP 监听不会自动领取任务。
具体部署命令见[远程 shape 服务指南](remote-shape-service.md)。

新发现入口的真实验收见 [TripoSR 服务发现发布验收](../reports/triposr-discovery-real.md)。

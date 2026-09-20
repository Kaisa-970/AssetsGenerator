# 可组合 RGB 图片缩放节点

日期：2026-09-21。新增 resize_image@1 Operator 与 ResizeImageAdapter，
在 CPU 示例和 ComfyUI 配置目录注册。输入输出均为 RGB PNG Artifact，参数
width/height/resampling 固定在节点绑定及 Core provenance 中。

提供 encode → thumbnail/model_input 的分辨率扇出模板，支持在画布修改尺寸。
宽高为 1–4096，精确拉伸到目标尺寸；不自动裁剪、补边或保持纵横比。
拒绝伪标为 PNG 的 JPEG、RGBA 及损坏输入，不携带原 EXIF/相机信息。
不声明相机、mask、depth 或 ImageWarp 转换能力。

验证：相关 **16 passed in 3.17s**，覆盖明确编码契约、非法尺寸编译拒绝、
最近邻实际像素、两实例共享输入及独立输出/来源、恢复不重做，以及单分支输出
丢失后的准确阻塞与旁支保留。ComfyUI 目录断言同步新增模块。
Ruff lint/format、mypy（131 个源文件）、两份 Operator YAML 字节一致及
`git diff --check` 通过。没有执行新的 GPU 模型或浏览器交互验收，也未重复完整套件。

新增全局 Operator 契约会影响基于该契约集合绑定的计划身份；不修改历史计划或
运行证据来绕过校验。测试只证明本模块的像素与编排行为，不是模型质量结论。

## 实际浏览器交互补验

在产品基线 `5fdec8c`，使用 CPU 示例服务和新增 `frontend/smoke/image-resize.cjs`。
浏览器加载原编码模板，通过目录添加两个 resize 实例并手工连线；表单设置
64×32、96×48 和 nearest，编译后上传 RGB 图片并启动。两个预览图片的
naturalWidth/naturalHeight 与表单尺寸一致，两节点实际输入均指向 encode 的输出。

最终核验运行 `dag_7ce47cb995fb4e119d11bc2c54f8c0fe` 成功，三个节点各一次 attempt。
等待显式 resume 请求返回 202 后再轮询空闲，完整节点记录不变，浏览器无 pageerror。
脚本退出码 0；服务已关闭。此前首次 smoke 也成功，但恢复检查未显式等待 POST
回执，因此补强脚本后用独立新运行重新验证，不将首次结果作为最终恢复证据。

外部证据位于 `<DATASET_ROOT>/resize-browser-cpu-v1/rechecked/`：browser.log、
resize-browser.json、resize-browser.png；运行 Store 位于父目录 editor/。
Node 语法检查、Prettier 和 git diff --check 通过。本补验不调用模型或 GPU。

## 共享编辑器目录

补齐普通模型配置的目录：标准 Operator 目录下，本地模型、SAM-only 和远程 Shape
编辑器也注册编码/缩放 CPU Adapter；ComfyUI 复用其已有注册，避免重复。
自定义 Operator 集合只注册其声明的实用模块，不向多视图专项契约强加图片端口。

启动与目录相关测试 **15 passed in 3.69s**；新增远程服务不可达时仍成功执行
encode→双 resize 的回归，并读取 128×128 输出；既有 SAM-only、ComfyUI-only 及
多视图专项目录启动回归通过。Ruff、mypy（131 文件）、git diff --check 通过。
此项没有运行新 GPU 推理或浏览器 smoke。

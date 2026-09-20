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

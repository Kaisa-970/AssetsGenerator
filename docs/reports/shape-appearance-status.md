# 生成结果外观事实提示

2026-09-23；实现提交 `5e91b74`（基于 `e48c57e`）。

## 原因确认

只读检查 `<DATASET_ROOT>/dual-backend-discovery-v1/trellis.sqlite` 中的
shape_metadata，backend_metadata.postprocess_mode 明确为
`geometry_fallback_no_texture`。结合此前原生 GLB 回读，本轮无纹理属于 Backend
后处理降级，不是发布时丢失外观。旧 runner 没有记录具体异常正文，无法从该记录
区分 CuMesh 失败与其他符合降级规则的 CUDA 错误；本轮没有重新推理。

## 实现

远端 shape 导入完成原始 mesh 身份和编码校验后，对已有的两种 postprocess_mode
白名单声明生成带该元数据的新 mesh Artifact，Blob 不变，原 Artifact 不改写。
运行输出和 provenance 引用新 Artifact，恢复时继续使用固定结果。服务声明只用于
说明后处理路径，不替代 GLB 实际外观检查；不增加 Operator 端口或通用服务协议字段。

编辑器新增只读 appearance 接口，先核验输出证据闭包，再检查 GLB primitive 实际引用
的标准纹理与 COLOR_0 属性。结果面板按来源节点/端口独立显示纹理、顶点颜色和已记录
的降级路径；无纹理不等于无材质，纯色材质仍可能存在。历史记录缺少后处理声明时明确
显示原因未记录，不补写或推断降级原因。QA 和运行成功状态保持独立。

## 验证与限制

- 后端相关回归 39 项通过；随后新增证据损坏案例，材质测试文件共 5 项再次通过。
- 前端材质显示测试 4 项通过：降级、历史未知、纹理与顶点颜色区分、无效计数拒绝。
- 前端 TypeScript/Vite 构建通过，保留现有大 bundle 提示。
- 修改文件 Ruff、mypy 通过，git diff --check 通过。

未重复完整测试套件、未做真实浏览器走查、未启动 GPU。仅检查标准 GLB 纹理引用和
颜色属性，不检查视觉质量、UV 正确性、纹理内容或扩展材质效果；不称为材质质量验收。

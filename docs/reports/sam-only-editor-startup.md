# SAM-only 编辑器启动核验

日期：2026-09-20。基线 `935bffc`，随后仅调整配置报错提示和测试。
复用现有 SAM sugar 环境和 checkpoint，无模型或 PyTorch 下载。

验收脚本调用 load_proposal_profiles、proposal_adapter_registry、NodeEditorExecution
和 create_editor_server，核验真实 SAM 资源、启动监听并读取目录（HTTP 200）。
显式将 Shape 加载函数替换为抛异常的 guard，整个过程没有调用它。
`remote_selected_image_asset_v1.yaml` 编译返回 execution_ready=true，目录无 image_build，
运行列表为空。检查结束后关闭本次 HTTP 服务。

总耗时约 16.35 秒：checkpoint 3.2 秒、环境 11.9 秒、一致性检查 1.0 秒。
这是本机一次启动观察，不是性能 benchmark。没有创建运行、没有 GPU 推理，
没有浏览器操作；远程配置使用离线占位身份，不表示远程服务或模型通过验收。
完整 CLI 参数转发由代码路径检查及配置测试支持，本次脚本直接调用组件。

仓库外证据：`<DATASET_ROOT>/sam-only-editor-startup/verify.py` 和 `validation.json`。
后续需用真实服务身份执行 SAM-only 配置下的完整浏览器流程。

## 真实双配置 CLI 与浏览器编译

随后使用 `remote_shape_cli serve` 核验并启动真实 TRELLIS.2 服务，再通过
`node-editor --proposal-config ... --profile sam-local --remote-config ...` 启动编辑器。
远程配置采用本次服务实际输出的 service_id/backend_digest，不再使用占位身份。
浏览器成功加载选区模板并点击编译，返回 execution_ready=true，运行列表为空。
保存截图和编译结果后关闭本次两个服务，未影响已有编辑器。

TRELLIS 权重核验 21.6 秒、环境 12.8 秒、一致性检查 1.3 秒；SAM checkpoint 4.0 秒、
环境 12.1 秒、一致性检查 1.0 秒。仅报告单次观察。
GPU 显示约 29% 活动且未列出所属进程，因此没有创建运行或执行推理。
这证明真实 CLI 配置、目录加载和浏览器编译，不证明 mask 审查、远程生成或发布完成。

外部证据：`<DATASET_ROOT>/sam-only-real-browser-v1/` 下 service.log、editor.log、
endpoint.json、browser-compile.json 和 compiled.png。完整浏览器执行仍待验收。

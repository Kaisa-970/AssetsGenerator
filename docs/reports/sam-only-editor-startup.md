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

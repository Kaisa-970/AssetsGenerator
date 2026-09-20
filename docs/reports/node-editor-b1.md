# 节点编辑器 B1 首版

已实现独立 React/TypeScript/Vite 构建、React Flow 画布与本机 Python 静态资源服务。支持节点目录、多实例、Pipeline input、连线/删除/改名、参数与绑定编辑、YAML 导入导出、草稿原子保存加载、后端静态编译及历史 BuildRun 状态展示。

后端仅编辑和编译，无执行 API。默认 CLI 目录来自 OperatorSpec，模型 registry 未加载；DraftEditor 可注入 AdapterRegistry，提供真实规格目录及参数/绑定检查。未实现 schema 驱动的完整参数表单，首版使用 JSON 编辑。CompiledPlan 以只读编译结果展示，未实现独立不可变计划画布模式。不得据此宣称 B2 或完整 B1 产品体验完成。

## 验证

- Python 定向：node editor、AdapterRegistry、CLI 共 32 passed。
- 实际本机 HTTP 页面自动化：加载多视图模板，4 节点/9 连线；后端编译成功；保存草稿、切换单图、重载草稿及再次编译通过，无页面脚本异常。
- 页面截图仅作为开发检查，未代替用户验收。草稿在仓库外。
- 静态编译结果明确 execution_ready=false。不存在 Backend 资源加载或模型推理。

## 边界

模型执行、运行管理与人工审查组件接入属于 B2。默认显示全部契约并不意味着每个算子已有可执行 adapter；未声明关系的复杂汇合由后端拒绝。诊断主要复用 compiler 错误文本，多数 node.port 可定位，全局诊断不能保证精确到 edge。

前端最终检查：9 项 Vitest 与 1 项 Playwright 浏览器回归通过，npm build 通过。Ruff、mypy（84 个源文件）、sdist/wheel 构建通过；已检查 wheel 包含 index.html 和静态 assets。未重复运行整个仓库全量测试。

## 交互修正

- 节点/边删除基于最新草稿状态连续更新，避免 React Flow 同一删除操作的两个回调互相覆盖；删除后清空相关选择及布局。
- 拖动采用 React Flow 的节点状态增量更新，松手后保存 layout，节点/边派生数据 memoize，避免每帧重建整个声明图。
- 新浏览器回归逐步拖动检查位置连续、视口不变、布局可保存；同时选中节点 C 和独立 A→B 边后按 Delete，确认节点、配置面板和保存草稿的边绑定全部删除。2 项浏览器测试通过。

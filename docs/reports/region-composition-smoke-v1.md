# 人工区域组合流程验证 v1

日期：2026-09-17。范围：恢复、检查历史、显式选定、人工区域选择和独立组件组合发布。

## 验证事实

- 363 项测试通过，Ruff format/check、mypy（40 个源码文件）、build 通过。
- 覆盖跨会话恢复、旧版无 run_id 记录跳过、检查冲突要求解释、新意见使旧选定失效、
  无效/空区域拒绝、材质往返、精确面编号记录、原子发布失败与 BuildRun 状态。
- 真实 DA3+TSDF/TRELLIS.2 候选浏览器链路通过：保存/确认、刷新历史、恢复、选定、
  生成图层 inside 方框选区、预览、组合发布。测试意见标为自动化 smoke，不代表人工质量验收。
- 重载组合 GLB：2 个独立组件，221,804 个面，分别保留 vertex 和 texture visual。
  总面数与 region_selection 的 source_face_indices 总数一致；发布 run.json 为 succeeded。
- 临时服务已关闭，未运行 GPU 模型或安装 PyTorch。浏览器曾因 CDN 加载失败重试，最终通过。

证据：`<DATASET_ROOT>/candidate-manual-alignment-smoke-v1/composition-170ae159d4d8414499695d921520fb4f/`，
以及同目录上级的 composition-editor.png。

## 边界

仅提供数值方框、按面中心选择；没有自由画笔/套索、边界面切割、接缝焊接、水密化或拓扑修复。
组合包可查看和追溯，但不是已通过 QA 的标准 AssetRelease。本报告不证明真实物体形状、
配准正确性或仿真质量，也不关闭完整 Phase 6。

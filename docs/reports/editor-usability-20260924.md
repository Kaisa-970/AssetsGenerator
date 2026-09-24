# 节点工作台交互收口与独立验证

日期：2026-09-24。基于 `5e91b74`，本轮工作区未提交。
实施范围见 [交付计划](../planning/editor-usability-20260924.md)。主 agent 把控范围、集成与回归；
三个子 agent 分别承担产品/参数交互、组件拆分/测试、独立代码与真实 HTTP 浏览器验证。

## 用户可见改动

- 中间 triangle_mesh 在 A/B 中也能打开模型，预览按钮包含节点与端口，避免混淆多网格输出。
- 输出列表提取为 RunOutputs；来源核验、幂等创建和参数绑定仍由原有执行逻辑管理。
- 参数字段和完整 JSON 的未应用编辑按节点保留；跨页提示并禁止新运行。应用或明确放弃后才能创建，既有运行的恢复不受草稿限制。
- 导入 BuildRun 的徽标明确为“历史”，画布常驻来源；不再无标识地套用到当前草稿。
- 预览端口与实际显示同步，空状态区分无运行、历史无节点、待运行、运行中及失败。
- 图片可以放大，Escape/关闭返回并保留来源；顶部可收起节点预览，1280×720 下可借助既有缩放控制选中节点。收展保留已读取图片，不重新执行。

## 独立验收与回归

CPU fixture 以真实远端 HTTP 导入三种小网格（无纹理并声明降级、顶点色、纹理），
各自经 canonical → QA → assemble → publish。浏览器通过真实编辑器接口检查各阶段
外观事实与 WebGL 预览，验证 shape/canonical 的 A/B 固定快照访问。
证据根 `<SMOKE_ROOT>/appearance-cpu-0924-c/` 含运行 Store、browser-config、browser-result 与截图。
不将 CPU box 解释为真实模型生成，也没有用户人工批准。

复现（新目录，无 GPU）：

```bash
# worktree 根目录，前端已 npm run build
PYTHONPATH=src:tests <MAIN_CHECKOUT>/.venv/bin/python frontend/smoke/appearance_server.py --root <NEW_SMOKE_ROOT>
# 另一终端，使用该服务刚生成的配置
node frontend/smoke/appearance-browser.cjs <NEW_SMOKE_ROOT>/browser-config.json
```

最终检查：前端 Vitest 72 项通过，定向 Playwright 与完整 68 项浏览器回归通过；
真实 CPU 外观走查 56 个 API 请求均成功、9 次 WebGL 预览及 3 轮中间 mesh A/B 通过。
后端 Ruff、format、mypy 通过；全量 pytest 首次长跑仍在收尾，已修正其中的历史测试前提和
自动远端收取等待契约，需以最终日志为准。

首次广泛回归发现历史测试未同步，已按实际契约修正：
- legacy contract 重构应剔除新增 text/masked 算子；保持原历史摘要常量不变。
- 编辑器显式启动持有自动远端收取循环，HTTP 测试改为先等 queued、再等待终态；增加派发计数验证 GET/幂等创建没有额外派发。
- 进程授权保存失败后，launcher 的退出是异步的；测试先断言保守占用，再有界等待真实退出，期间始终验证推理命令没有执行。
- 前端断言使用具体字段错误与包含端口的预览按钮，不再把全局警告误计为字段错误；画布测试以显式相机操作建立基线，不假设 fitView 恰好为 1。

## 交用户验收

使用常规节点编辑器地址，先选择 CPU 图片模板，上传图片并启动。重点体验：
点击节点看图片 → 放大/关闭 → 收起预览调整画布 → 修改参数 → 切换运行页检查未应用提示。
有模型结果时再看 shape/canonical/publish 的外观事实和 A/B。操作说明见
[中文指南](../guides/node-editor.md#修改参数与查看中间结果)。

未重跑 GPU、未评判真实模型质量、未验收六步完整真实资产主路径、未合并主分支。
接口中的外观事实不验证纹理内容、UV 或视觉质量；远端可达性/资源不属于编译预检结论。

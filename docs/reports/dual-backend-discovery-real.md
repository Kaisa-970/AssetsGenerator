# 同一节点切换两个真实模型服务验收

2026-09-23，代码基线 `75d7cf4`。本轮只补验收记录，不修改模型或执行器。

## 范围与方法

从浏览器通过 URL 添加 TripoSR、TRELLIS.2 两个服务，将同一 `shape_generation@1`
节点切换 Backend。每轮使用 `remote_shape_asset_v1` 的五节点链：生成 → 坐标规范化
→ 几何 QA → 组装 → 发布，输入为同一张既有 RGBA，参数 seed=42、pipeline_type=512。
TripoSR 不使用这两个通用推理参数。复用已有独立环境和权重，没有安装 PyTorch 或下载模型。

两个服务各自启动正式 `remote_shape_cli work` 自动领取任务，没有手工 execute-next。
TripoSR 整条链完成后才提交 TRELLIS.2；不同服务数据库没有共享 GPU 锁，串行顺序由本次
验收脚本保证，不能将其解读为跨服务自动资源调度。

首次脚本完成 TripoSR 后，在第二轮创建请求之前超时；没有产生第二次 TripoSR 推理。
保留第一次全部证据，用续跑脚本重新打开页面、导入相同五节点模板、在 shape 节点切换
TRELLIS.2、上传同一图片并启动。因此这是两段浏览器会话验收，不是一次不中断的录制。

## 验证记录

针对服务 descriptor、SDK、自动领取循环、CLI 和进程重启的五个测试文件：
41 passed（10.47s）。未重复完整测试套件。

两轮五节点均 succeeded，各节点恰好一次 attempt：

| Backend | Run | 顶点 | 三角面 | 原生 / 发布外观 |
| --- | --- | ---: | ---: | --- |
| TripoSR | `dag_b448463c251a4acd99547c4194d41a72` | 42,257 | 84,383 | 顶点颜色 / 顶点颜色 |
| TRELLIS.2 | `dag_b392520e2d7b4586b7237275c542d1ac` | 1,285,164 | 2,598,104 | 无纹理 / 无纹理 |

独立脚本 `verify_switch.py`、`verify_provenance.py` 均退出码 0：

- 两次 shape 输入 ArtifactRef 完全相同，节点集合和 Operator 相同；Backend 摘要、服务身份和提交键不同。
- TripoSR 完成时间早于 TRELLIS.2 开始时间。
- descriptor 与实际 native_frame 一致：TripoSR 为 +Z，TRELLIS.2 为 +Y，均为 relative_unit。
- 两个父运行的完整 Artifact 证据闭包通过；shape provenance 指向正确运行、节点和共同输入，ID 不碰撞；release 指向正确 asset。
- 发布 GLB 均可用 trimesh 回读。另行回读 shape、canonical 和 publish 三阶段，外观类别一致。
- TRELLIS.2 的原生结果已经不含纹理，不能把本轮成功解释为带材质生成验收，也没有证据表明是发布丢失纹理。
- 两轮 QA 均为 warn：相对尺度、机械估计朝向；几何相关检查通过。collision 未请求，render-back 未实现，二者 skipped。
- 完成后通过 API 显式 resume，两个运行的完整 node_states 与完成时完全一致，没有新 attempt；此项不是服务重启或推理中断恢复。

`validation.json`、`provenance-validation.json`、`appearance-validation.json` 与 `*-restored.json` 保存独立核验结果。
`git diff --check` 通过。本轮仅提交验收报告与文档索引，未修改产品代码。

## 证据与边界

仓库外证据位于 `<DATASET_ROOT>/dual-backend-discovery-v1/`：`switch.cjs`、
`continue-trellis.cjs` 及日志，`*-submitted.json`、`*-completed.json`，发布输出、
页面截图、服务配置、SQLite 作业记录和 Store。脚本遇到已有提交文件时拒绝重复提交。

此轮验证现有独立进程 Backend 经服务发现入口的互换；不代表任意第三方模型已通过
`ShapeModelService` 回调 SDK 的真实验收。单张输入不是代表性质量 benchmark，也不评判
两个模型的视觉质量。没有执行本轮真实 GPU 推理中 SIGKILL；CPU 中断测试不能替代它。

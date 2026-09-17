# Open3D TSDF 接入验证 v1

日期：2026-09-17。

## 范围与环境

独立 CPU Open3D 0.19.0 runner，加 DA3-Base 真实 GPU frontend。
复用已有 `sugar` 环境运行 Open3D，DA3 继续使用独立 DA3 环境；本轮未安装包，
未下载或替换 PyTorch。测试与 Core 不依赖 Open3D。

数据、脚本、日志与生成资产保存在 `<DATASET_ROOT>/open3d-tsdf-smoke-v1/`，不提交仓库。

## 已知几何验证

真实 Open3D 输入：64×64 RGB、恒定 2 meter 深度，fx=fy=60、cx=cy=32，
`T_world_camera` 平移为 (3,0,1)。预期表面位于 world z=3，中心约 (3,0,3)。
实际输出中位顶点为 (2.98,-0.02,3.0)，符合 voxel 0.02 的容差；
输出 11236 顶点、22050 三角面，GLB 重新加载后顶点 RGB 保留为 (200,40,80)。
该检查直接验证 world-to-camera 求逆方向和 depth_scale=1.0，非视觉估计。
证据：`synthetic.py`、`synthetic/result.json`、`synthetic.log`。

## 完整发布 smoke

使用固定 DA3 revision 和 DA3-Base snapshot，输入为官方 SOH 两张图；身份沿用
[DA3 双帧报告](da3-frontend-smoke-v1.md)。本次重新执行 frontend，未复用旧输出。
通过 `ResolvedPlan` 绑定真实 DA3 和 Open3D，显式选择 `preserve_mesh`。

完整链路通过，最终 GLB 重新加载确认 70740 个顶点、96610 个三角面、35651 种顶点颜色。
适配器、Core 空间/端口校验、canonicalization、组件 provenance、QA 和 release 均执行。
QA overall 为 warn：relative scale、机械估计 forward；render_back 为 skipped。
最终成功运行总耗时 23.387 秒；不将一次 smoke 耗时作为性能基准。
证据：`run.py`、`run.log`、`result.json`、`release/` 和 `store/`。

首次真实进程调用发现适配器名 `open3d.py` 遮蔽了环境包，现已改为 `open3d_tsdf.py`，
补充脚本目录导入回归测试并重新执行真实链路成功；首轮失败证据保存在 `first-attempt.log`。

## 自动检查

Open3D adapter/runner 共 51 项测试通过，包含空间方向、mask/invalid、尺度、输入完整性、
进程协议、包名遮蔽与顶点颜色。全量 pytest 334 passed；Ruff format/check、mypy（36 个源码文件）、
sdist/wheel build 和 git diff --check 均通过。wheel 已检查不含遮蔽 Open3D 的模块名。


## 限制

SOH 是场景输入，没有物体 mask。该运行不证明物体隔离、闭合网格、重力方向、
米制尺度或代表性重建质量；默认 -Y 仅为显式坐标约定。
TSDF 不补全不可见表面，不提供 UV 烘焙或独立 PBR 纹理。
Phase 5 CameraRegistration/render-back gate 未实现，完整 Phase 6 仍未关闭。

## 环境身份加固复验

2026-09-17 增加 Open3D 专用环境探针，保留既有 TorchMCubes 身份行为。
记录 Python 调用路径/内容摘要、Open3D 构建配置与安装元数据、Open3D/NumPy/Pillow/trimesh
实际安装文件摘要；执行前后环境不一致则拒绝发布。同版本文件修改已有回归测试。
真实环境探针输出见 `environment-identity.json`，完整真实链路再次通过，见
`identity-validation/result.json`、`identity-validation/release/provenance/`。
重建 provenance 已确认包含环境摘要与四个关键包的内容摘要。
内容哈希会增加运行开销；该身份不覆盖所有系统库或驱动。

环境身份加固后最终检查：337 项 pytest 通过，Ruff、mypy、sdist/wheel 构建和 git diff --check 均通过。

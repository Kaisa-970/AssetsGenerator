# Open3D TSDF Reconstruction Backend

## 范围

Phase 6 使用 Open3D 的 CPU ScalableTSDFVolume 作为首个网格融合 Backend。
Core 通过独立进程调用，不增加 Open3D、CUDA 或模型依赖。输入是同一静态对象或场景
的 RGB、深度和已估计相机；输出是带顶点颜色的三角网格。无学习式补全、无 UV 烘焙、
无隐式配准，不把 DA3 相对尺度升级为米制尺度。

## 输入与空间

`reconstruction@1` 的 observation、camera、depth 通过 view_id 对齐；首版要求每个
观测都有一个相机和一张深度。相机需是无畸变 pinhole，外参为 `T_world_camera`，
深度处于对应 camera frame。depth、points 使用相同 unit，points frame 与共同 camera
world frame 一致。points 不参与 TSDF 融合，只用于已有空间契约校验。

Open3D 接收 `inverse(T_world_camera)`，即 world-to-camera；深度使用 float32 和
`depth_scale=1.0`。融合 RGB，提供 mask 时排除背景；无效深度不参与融合或尺度计算。
输出保留输入 world frame 和 unit。默认 `up_axis=-Y` 是明确的用户坐标约定，
不是重力估计；调用方可指定其他有效 axis，forward 保持 unknown。

## 相对尺度规则

`median-valid-positive-depth-v1`：所有输入有效正深度的中位数作为 reference_depth，
voxel_length = reference_depth × voxel_size_ratio（默认 0.01），
sdf_trunc = reference_depth × sdf_trunc_ratio（默认 0.04），
depth_trunc = reference_depth × depth_trunc_ratio（默认 3.0）。

参数必须有限且为正，sdf_trunc 不小于 voxel_length。实际尺度、配置比率、算法版本、
runner 摘要、Open3D 版本均记录在执行 metadata/provenance 中。
环境身份探针记录未解析符号链接的 Python 调用路径、解释器内容摘要、Open3D 构建配置、
安装元数据及 Open3D/NumPy/Pillow/trimesh 的实际安装文件内容摘要（排除 pyc 缓存）。
环境摘要在融合前后校验，变化时拒绝结果；同版本本地修改也会改变身份。
该摘要覆盖选定解释器与关键包，不代表全部系统动态库、驱动或操作系统镜像身份。
这些默认值是工程起点，不是物体质量保证。缺失深度、无有效像素或空网格必须显式失败。

## 外观与来源

输出白色 PBRMaterial 仅作为结构化摘要；RGB 融合颜色在 mesh 的 vertex colors 中。
调用 `build_multi_view_asset(..., export_appearance_mode="preserve_mesh")` 保留颜色。
多视图默认仍为 `apply_material`，兼容输出独立材质的 Backend；模式进入 BuildRun 输入、
GLB 身份和 export provenance，禁止靠是否存在 UV 或颜色自动猜测外观来源。

整体组件标记 reconstructed；Backend 不签发 provenance ID，由 Core 根据观测、
CameraCollection、depth 和 points 证据生成。TSDF 可能存在孔洞或背景表面；不声明闭合、
完整、可碰撞或物体级隔离。没有 mask 的场景输入只适合流程 smoke。

参考：https://www.open3d.org/docs/release/tutorial/pipelines/rgbd_integration.html

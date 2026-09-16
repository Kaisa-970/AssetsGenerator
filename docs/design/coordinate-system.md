# Coordinate System and Frame Contract

**版本**：v0.4.1

## 1. 内部坐标约定

```text
handedness: right-handed
up axis: +Z
forward axis: +X
metric length unit: meter
non-metric length unit: relative_unit
transform storage: 4x4 float64 matrix
transform convention: column vector
```

变换命名为 `T_target_source`：

```text
p_target = T_target_source * p_source
```

相机外参统一保存 `T_world_camera`。

## 2. FrameGraph

`FrameGraph` 是坐标 frame 及其空间变换边的集合，不是图像 view 的关联图。

```python
@dataclass(frozen=True)
class Frame:
    frame_id: str
    kind: str
    handedness: str
    up_axis: str
    forward_axis: str | None
    unit: str

@dataclass(frozen=True)
class TransformEdge:
    source_frame_id: str
    target_frame_id: str
    matrix: list[list[float]]
    provenance_id: str
    confidence: Confidence | None

@dataclass
class FrameGraph:
    frames: dict[str, Frame]
    transforms: list[TransformEdge]
```

`Frame` 声明坐标约定，`TransformEdge` 声明两个 frame 之间的空间变换，两者职责不同。长期接口保留 revision 和闭环一致性验证能力。

## 3. Phase 1 最小实现

Phase 1 只支持一条线性 frame 链：

```text
backend_native
      ↓
asset_canonical
      ↓
gltf_export
```

不实现：

```text
通用图搜索
动态 frame 发布
多条 active 路径选择
TF2 类时序数据库
复杂闭环优化
```

Phase 1 的三个 frame 都由固定 Pipeline 创建和登记：

```text
backend_native    由 BackendNativeFrame 转换为 Frame
asset_canonical   由 CanonicalizationOperator 创建
gltf_export       由 GLTF2Profile 创建
```

每个 Phase 1 mesh Artifact 必须声明所属 `frame_id` 和 `unit`。CanonicalizationOperator 必须同时输出 canonical mesh、`asset_canonical` Frame、变换边和 AssetSpatialInfo。

## 4. BackendNativeFrame

Shape Backend 必须显式输出原生 frame 声明：

```python
@dataclass(frozen=True)
class BackendNativeFrame:
    frame_id: str
    handedness: str
    up_axis: str
    forward_axis: str | None
    forward_status: str  # declared | estimated | unknown
    unit: str
```

`CanonicalizationOperator` 同时接收 mesh 和 `BackendNativeFrame`，不能只根据 mesh 字节猜测轴与单位。

## 5. V1 Canonicalization

V1 使用确定性机械规则：

```text
1. 将 Backend native up 转换到内部 +Z
2. 若 forward_axis 已声明，则将其对齐到内部 +X
3. 若 forward_axis 未知，则把水平面投影后 AABB 的最长边对齐到 +X
4. 最长边方向仍有 180 度歧义时，选择使原生坐标中最大绝对分量为正的固定方向
5. 将 AABB 最低点平面中心平移到原点
6. 未知公制尺度时按最大包围盒边归一化为 1.0
7. scale_status = relative
8. forward_status 保持 declared、estimated 或 unknown
```

最长边退化或近似对称时使用 Backend native 水平轴经过 up 对齐后的投影作为固定 yaw 参考。所用规则、阈值和 tie-break 版本必须进入 canonicalization provenance。该规则保证可重复变换，但不宣称得到真实语义前方。

只有后续 Operator 获得可靠证据时，才生成新的 canonical Artifact。

## 6. AssetSpatialInfo

```python
@dataclass(frozen=True)
class AssetSpatialInfo:
    canonical_frame_id: str
    aabb: AABB
    obb: OBB | None
    scale_status: str  # metric | relative | unknown
    unit: str
    forward_status: str  # declared | estimated | unknown
```

## 7. Export Profile

ExportOperator 必须选择显式 profile：

```text
GLTF2Profile
USDProfile
IsaacUSDProfile
```

每个 profile 定义：

```text
目标轴与 handedness
目标单位和 scale_status 要求
变换保留或烘焙策略
法线、切线和 winding 转换
相机局部轴约定
材质通道映射
碰撞与物理字段映射
```

Phase 1 只实现 `GLTF2Profile`。相对尺度资产可以导出 GLB，但要求真实米制的 Isaac profile 必须拒绝它，直到完成尺度标定。

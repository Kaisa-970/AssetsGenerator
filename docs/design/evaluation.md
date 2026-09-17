# Validation and Evaluation

**版本**：v0.4.1

## 1. Per-asset QualityReport

```python
@dataclass
class QualityCheck:
    check_id: str
    applicable: bool
    value: float | int | str | None
    threshold_profile: str
    status: str  # pass | warn | fail | skipped
    reason: str | None
    evidence_artifacts: list[ArtifactRef]

@dataclass
class QualityReport:
    profile: str
    checks: list[QualityCheck]
    overall_status: str
```

可选输入缺失时，对应检查必须设置 `applicable=false` 和 `status=skipped`。

## 2. V1 Geometry QA

V1 fail 条件：

```text
GLB 无法加载
Blob digest 校验失败
空间 Artifact 缺少 frame 或 unit
Mesh 为空或包含非有限坐标
mandatory provenance 缺失
Pipeline 必需节点失败
```

V1 warn 条件：

```text
non-manifold
connected components 超出阈值
forward_status 为 estimated 或 unknown
scale_status 不是 metric
```

## 3. Render-back QA

Render-back 依赖：

```text
原始 CameraRecord（若已知）
ImageWarp
Backend camera hint（若有）
CameraRegistration 结果
```

尚未接入 CameraRegistration 的 Pipeline，所有 render-back 检查必须输出：

```json
{
  "applicable": false,
  "status": "skipped",
  "reason": "camera registration is not implemented for this pipeline"
}
```

Phase 5 引入最小 registration gate。具体算法可以变化，但 profile 必须显式定义：

```text
registration metric name and version
threshold
minimum visible area
maximum reprojection or silhouette error
failure behavior: skipped or warn
```

只有 gate 通过才计算 Silhouette IoU、LPIPS、SSIM、Depth consistency 等像素级指标。未经校准的注册输出使用 `score`，不能称为 confidence。

Phase 5 当前显式延期。Phase 6 多视图 Core 中的估计 camera 不等同于通过 registration gate；在 Phase 5 恢复并完成相应契约前，render-back 仍保持 `applicable=false, status=skipped`。

## 4. Offline Benchmark

接入第二个 Shape Backend 前建立：

```text
BenchmarkDataset
EvaluationRun
MetricSuite
ComparisonReport
```

BenchmarkDataset 至少覆盖：

```text
常见刚性物体
薄结构和细杆
对称物体
反光或低纹理表面
遮挡和截断
复杂背景
透明背景
```

比较必须固定：

```text
数据版本和 mask
预处理
canonicalization
GLTF2 Export Profile
渲染器
QA profile
硬件与 seed 策略
```

报告包含成功率、质量指标、耗时、峰值显存、失败分布、固定视角 turntable 和预定义人工评分。

当前 Phase 3 观察结果与下一轮人工评分规范见
[初版 VOC 基线报告](../reports/phase3-baseline-v1.md)。本轮为排序抽样的流程验证，
不视为代表性 BenchmarkDataset，也不把用户精选结果当作无偏质量评测集。

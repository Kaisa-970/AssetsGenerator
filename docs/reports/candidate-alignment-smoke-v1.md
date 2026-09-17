# 显式候选对齐验证 v1

日期：2026-09-17。范围为已有候选包的显式空间变换和诊断预览，不验证配准质量。

## 输入和参数

复用 completion-candidate-smoke-v1 的真实 DA3+Open3D 重建与 TRELLIS.2 生成候选，
候选 Artifact 为 `sha256:a15779c1ed64b51a390b949a8277c7a1ac07f83fb5791b3f39a5df6fc2709461`。
本次没有运行 GPU 模型，也没有下载或安装依赖。

人为指定测试变换：GLB 坐标绕 +Y 旋转 90°，统一缩放 1.1，平移 (0.1, 0.2, -0.1)。
源/目标使用各 release identity 限定的 gltf_export frame；该参数不是估计配准结果。
证据位于 `<DATASET_ROOT>/candidate-alignment-smoke-v1/`：run.py、validation.json 和 aligned/。

## 验证事实

- 生成模型 296,422 个面，重载后世界坐标顶点符合给定矩阵。
- 对齐 GLB 的内嵌纹理及 UV 与原模型相同；原候选引用通过 digest 校验。
- 发布包含独立 aligned.glb、原始两份 GLB、SpatialTransform、provenance 和对齐 manifest。
- 浏览器实际点击并加载 overlay.glb、aligned.glb、reconstructed.glb；临时 HTTP 服务已关闭。
- 350 项测试通过，包括旋转/缩放/平移、场景节点变换、纹理与顶点颜色并存、
  schema 往返、错误 frame/unit/kind、非法矩阵、输出覆盖拒绝和发布失败清理。
- Ruff format/check、mypy（38 个源码文件）、build、git diff --check 通过。

对齐 manifest：`sha256:f3735d004c794ee77cebc7e42dc16c25be9891a85b616381300013c3edff5aba`。

## 边界

叠加预览的青色和橙色用于区分来源，不是区域标签，也不是融合资产。
状态仍为待检查；没有自动配准、表面保留验证、水密保证或几何约束 completion。
本次仅证明显式变换与交付流程可运行，不据此声明两份模型在几何上已经对齐。

## 运行记录补齐

审查后补充最小 BuildRun 与 align_candidate/materialize_alignment 两个 NodeAttempt。
provenance.run_id 现在指向 Store 中可查询的运行记录；成功输出携带 run.json，
预览或原子目录发布失败时运行及发布节点记为 failed/release_failed。
新增成功回溯、契约失败、预览失败和 rename 失败回归测试，全量 354 项通过，
Ruff format/check、mypy、build 和 git diff --check 通过。
上面的真实资产 smoke 属于补齐前记录，未重跑模型或将旧 provenance 改写为新运行；
新增运行状态链路通过上述自动化回归验证。

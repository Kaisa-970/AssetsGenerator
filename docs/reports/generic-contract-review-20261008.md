# 通用能力契约审查修复（2026-10-08）

基线：main `7459627`；本报告对应其后的未提交修复，未部署远端模型。

## 修复范围

- 补入 SAM3D 发现模块及身份绑定回归；构建后的 wheel 已核对包含该模块。
- 动态空间端口保留精确 frame_id/unit，编译和逐项运行时校验均拒绝错配；旧端口没有声明这些字段时不改变其序列化身份。
- GLB 导入前加载场景，核对有限顶点、非空面和索引范围；测试覆盖非法 accessor、非有限顶点、越界索引和截断 buffer。
- StructuredValue 的媒体描述为 application/json；SemanticInfo@1.0 校验字段、类型以及严格 JSON，覆盖 HTTP DAG 执行与重开恢复。其他尚无验证器的结构化输出 schema 在发现阶段拒绝。
- 通用输出当前每端口只支持 cardinality one；多个独立标量端口可用，可选/集合输出在发现阶段拒绝。
- 通用远程节点纳入已有严格复用检查，修复现有 CPU 图片链路中的复用回归；没有改写恢复器或放松身份检查。

## 验证事实

- 后端全套运行：1619 passed，397.95 秒；一项既有 Pillow 弃用提醒。
- 全套启动后补充多输出及三项损坏网格测试，并整理无行为变化的导入位置；最终源码定向重跑 151 passed，14.75 秒。没有把这轮定向结果与全套相加，也不称最终冻结源码全量验收。
- Ruff check、format check、git diff --check 和 Python sdist/wheel 构建通过。
- mypy src 仍有 6 个错误，位于 node_editor_execution.py、node_editor_services.py 和 model_service_descriptor.py；独立基线源码检查也能复现这些错误。本轮未宣称 mypy 通过。
- 未运行 GPU 或浏览器验收；几何可加载不证明模型质量、实际尺度或服务坐标声明真实性。

## 边界

结构化输出目前新增闭环的是 SemanticInfo@1.0，不代表全部 STRUCTURED_KINDS 都可导入。
空间输入比较的是端口和 Artifact 的声明；通用空间输出仍依据服务声明记录坐标，
不能由任意 GLB 字节证明服务真实使用了该坐标系。集合/可选输出仍待协议和导入器共同实现。

# Artifact Store

**版本**：v0.4.1

## 1. 职责

Artifact Store 负责：

```text
Blob 内容寻址
Artifact manifest 存储
digest 校验
位置解析
staging / commit / abort
BuildRun 索引
可选缓存查询
```

身份定义见 [asset-ir.md](asset-ir.md)。URI 不参与 Blob 或 Artifact identity。

## 2. 最小接口

```text
put_blob_staged
put_manifest_staged
commit
abort
resolve_blob
get_manifest
verify_digest
```

`resolve_blob(digest)` 返回零个或多个 `BlobLocation`。调用方不能把某个 URI 当作永久地址。

## 3. 原子提交

Worker 先将 Blob 和 manifest 写入 staging。所有 digest 和 schema 校验成功后，Store 一次性发布可见索引。失败或取消时执行 abort。

## 4. Job 视图

```text
jobs/run_000001/
├── input/
├── observation/
├── geometry/
├── materials/
├── qa/
├── asset.json
├── release.json
├── pipeline.json
└── run.json
```

该目录只是便于浏览的物化视图。权威身份来自 Artifact manifest、AssetDefinition 和 AssetRelease。

## 5. 缓存与重跑

```text
cache_key = hash(
    operator implementation
    + backend/model version
    + parameters
    + input Artifact identities
    + structured value digests
    + relevant environment constraints
)
```

```text
cache reuse
    复用已有 Artifact，并创建新的 ExecutionOutput(cache_hit)

rerun
    实际调用 Backend，记录本次输出 digest
```

确定性等级：

```text
exact        可验证输出 digest 相同
seeded       重跑按指标容差比较
best_effort  不声明输入决定唯一输出
```

`best_effort` 的输出 digest 仍是该次内容的稳定指纹，也可以缓存；实验重跑不能用 cache hit 代替。

## 6. Phase 1 范围

Phase 1 使用本地文件系统和简单索引，只实现写入、解析、校验和原子提交。不实现跨集群 Store、垃圾回收、复杂淘汰策略或远程复制。

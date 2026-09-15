# AssetsGenerator

通用 Real-to-Sim 资产生成管线的设计与后续实现仓库。

当前阶段聚焦一个真实的单图到结构化 3D 资产 vertical slice：

```text
Single RGB
    + Provided Mask
    -> RGBA Prepare
    -> One Real Shape Backend
    -> Canonical GLB
    -> AssetDefinition / AssetRelease
    -> Provenance and Geometry QA
```

设计入口：

- [Architecture](docs/architecture.md)
- [Asset IR](docs/asset-ir.md)
- [Coordinate System](docs/coordinate-system.md)
- [Pipeline Contract](docs/pipeline-contract.md)
- [Artifact Store](docs/artifact-store.md)
- [Evaluation](docs/evaluation.md)
- [Roadmap](docs/roadmap.md)

# Collision Generation v1

## Scope

Phase 8 v1 adds an explicitly requested, single-asset collision postprocess. It derives one
deterministic convex proxy from the canonical visual mesh and publishes a new immutable
`AssetDefinition` and `AssetRelease`. Existing image and multi-view pipelines do not request this
output by default.

This slice does not claim metric calibration, exact concave collision, mass, friction, rigid-body
behavior, articulation, USD/IsaacUSD compatibility, or runtime synchronization.

## Contract

`collision_generation@1` accepts exactly one canonical `triangle_mesh` and returns a
`collision_mesh`. The output preserves the input `frame_id`, `unit`, `up_axis`, and `forward_axis`.
The release-facing `geometry/collision.glb` is a separate `gltf_asset` in `gltf_export` coordinates.
The two identities must not be interchanged.

The current method is `convex_hull_v1`:

- load every scene geometry in stable node-name order;
- apply each node transform and combine its vertices;
- reject empty, non-finite, or lower-dimensional input;
- run CPU QHull through SciPy/Trimesh with fixed options;
- normalize vertex and face ordering before GLB serialization;
- reload and require a finite, non-empty, convex, watertight, positive-volume result.

A convex hull can bridge concavities and is only a conservative collision proxy. The provenance
records the algorithm, QHull options, NumPy, SciPy, and Trimesh versions. It does not identify the
native QHull build independently from the installed SciPy distribution.

## Identity and publication

The source asset and release are never modified. V1 rejects assets with an existing collision mesh
and releases that already reserve any collision output path. It also rejects unsafe release paths
and the reserved materialization names `asset.json`, `release.json`, and `run.json`.

The derived AssetDefinition uses a deterministic new `asset_id`, starts at `asset_version=1.0`,
preserves the source geometry, appearance, spatial, semantic, physics, observation, and component
fields, appends the collision reference, and appends a collision QualityReport. Direct provenance
is emitted for collision generation, collision export, collision QA, and the derived asset.

The new release reuses every source file ArtifactRef exactly and adds:

```text
geometry/collision.glb
qa/collision-quality-report.json
provenance/collision.json
provenance/collision-export.json
provenance/collision-quality.json
provenance/collision-asset.json
```

Publication is atomic and has a dedicated `materialize_release` NodeAttempt. A failed publication
leaves no output directory and persists a failed BuildRun.

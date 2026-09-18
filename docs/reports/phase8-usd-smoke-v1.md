# Phase 8 Standard USD Rigid Export v1 Smoke

**Date**: 2026-09-18

The existing metric TripoSR rigid asset was exported with the optional OpenUSD 26.5 CPU SDK in the
existing Core environment. No model inference, GPU execution, or PyTorch download was needed.
The exporter preserved the AssetDefinition and source release files, added a canonical Z-up meter
USD with visual vertex colors and flat normals, separate guide-purpose convex collision, and the
explicit mass, center of mass, inertia and friction evidence.

The SDK reopened the authored stage and checked geometry, transforms, materials, collision APIs,
stage units and reconstructed inertia. Publication completed with a succeeded BuildRun and
per-output provenance. All generated evidence is outside the repository under
`<DATASET_ROOT>/phase7-sam-smoke-v1/usd-rigid-smoke-v1-final` and the adjacent Artifact Store.

```text
source release: sha256:6b48c680e13aa99333325bfa646f5457410f053293ddc59da36a2c88a1865d47
run: run_47e9eb82b46140f0b1edf3aaaf3ff88f
AssetDefinition (unchanged): sha256:b23ed4b6e64dbe498713ffb055ad28682e58c091aad86da0fd2858d4ff58eb1b
USD: sha256:5408d4a3d59e678d73acc5df6c5e454b387691bb5243e5f786de8b86748e3b40
QA: sha256:458ae689fbb4e3d75c49ef8ea745d4c8724049a76b5a839f9b24d328c6d98d8d
AssetRelease: sha256:556c75562c8845e2da68ae11580fb299a2d40ff7d602f568c2b78c7b30eedc27
entry: geometry/usd/asset.usda
```

Separate SDK tests cover PNG texture/UV/factor preservation, multiple materials, mirrored and
nonuniform transforms, explicit and absent normals, repeated-eigenvalue inertia, unrepresentable
float32 physics, custom canonical frame identity, and failed or concurrent publication. The real
asset smoke has vertex colors rather than texture images; it does not independently validate a
real textured model or render quality.

This establishes a standard USD export workflow only. The physical values remain caller-supplied
smoke fixtures. Isaac Sim import, simulation stability, contact behavior, runtime synchronization,
and articulation have not been tested or claimed.

Final verification: 569 tests passed with the optional SDK installed. Ruff lint/format, mypy,
source/wheel builds, YAML mirror comparison and `git diff --check` passed. Independent review
found no remaining P1/P2 after the numerical and appearance fixes.

# Phase 8 Collision v1 Smoke

**Date**: 2026-09-17

## Facts

The `build-collision` CPU workflow was run against an existing TripoSR release in
`<PHASE7_EVIDENCE_ROOT>/store`. It completed without invoking a model Backend or GPU.

The generated canonical collision artifact reloaded as one geometry with 1,054 vertices and 2,104
faces. Its metadata remained `asset_canonical`, `relative_unit`, up `+Z`, forward `+X`. Collision QA
passed loadability, convex/watertight positive-volume geometry, spatial preservation, direct
provenance, and source release verification. All nine source release files were verified
byte-for-byte in the derived release.

The smoke establishes workflow execution, identity, traceability, publication, and basic geometry
validity for this one release. It is not a representative collision-quality benchmark and does not
establish metric or physics correctness.

## Evidence

```text
source release: sha256:6e4c8b535cfb1dc97e08a20574b1baa8887d3485e0409417f4f37b33d59c59ad
run: run_a96feee1bcb649e7a8a70b03786cbdab
derived AssetDefinition: sha256:e71325e8e5fa153cd6d7e336cbd055c5f663769aa58d0e0dc51e637f4de1fd6e
derived AssetRelease: sha256:2f345c786b8c905a85db43e46bc8de5ff3a79fe57e40869b0d6c5e5dc2ae3a22
canonical collision: sha256:c0c82d77dee249403b0df67b61e76448ae2496f2231c394812c015c51bd025e4
release collision GLB: sha256:7e7343921127f0c505426f95044517edef81cc60a7eeabd91981364142237b60
collision QA: sha256:a45df62ba95cf7efa2944a2494102ca4c3a8975ba6943699a7720818bc863a08
```

These identifiers describe repository-external evidence and are not committed as generated assets.

# Phase 8 Explicit Rigid Body v1 Smoke

**Date**: 2026-09-17

The explicit rigid-body workflow ran on the meter-unit TripoSR release produced by the Phase 8
metric-scale smoke. It reused the canonical visual and collision Artifacts and all release files,
persisted the caller JSON as evidence, created typed `PhysicsInfo`, emitted QA and provenance, and
published a succeeded BuildRun without model or GPU execution.

The supplied mass, center of mass, inertia tensor, friction, and restitution are smoke-fixture
values. This run verifies contract enforcement and traceability; it does not establish their
physical accuracy or simulator behavior.

```text
source release: sha256:f2e511f1cd636aebde8e208982c7b0a5d892dcece59cce8d6cde0b9a6261d40f
run: run_c20ad2608e7d45149ab6443a4ad80535
properties: sha256:1a37e49b238008ec46dd11b4306ee2bbcc743bb22bad8f13895f2dec506c4934
AssetDefinition: sha256:b23ed4b6e64dbe498713ffb055ad28682e58c091aad86da0fd2858d4ff58eb1b
AssetRelease: sha256:7fa3040c016222656391fae8800fe2249f0a562600e3bfc5e93787f4c91d9ab1
QA: sha256:583251980ef945ab47bf1f64c7f29d167b67f5960fe9eb8f218d90a5960eacbc
```

All generated artifacts remain outside the repository.

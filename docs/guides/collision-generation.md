# Collision Generation

Generate a deterministic convex collision proxy from an existing release:

```bash
assets-generator build-collision \
  --release '<ASSET_RELEASE_ARTIFACT_ID_OR_REFERENCE_JSON>' \
  --store '<STORE>' \
  --output '<NEW_RELEASE>' \
  --method convex-hull
```

The command reads the canonical visual mesh referenced by the source AssetDefinition. It creates a
new asset and release; the source artifacts remain unchanged. The output contains the original
release files plus `geometry/collision.glb`, collision QA, provenance, and a new `run.json`.

V1 accepts exactly one visual mesh and no pre-existing collision mesh. Its convex hull may cover
holes and concavities, so it is suitable as a simple proxy rather than an exact collision surface.
Relative-scale assets remain relative-scale assets; this command performs no metric calibration.

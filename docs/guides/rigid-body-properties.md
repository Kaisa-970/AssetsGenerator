# Explicit Rigid Body Properties

Create a JSON file bound to a metric AssetRelease and its sole canonical collision mesh:

```json
{
  "schema_version": "1.0",
  "source_release_id": "sha256:<ASSET_RELEASE_ID>",
  "source_asset_id": "sha256:<ASSET_DEFINITION_ID>",
  "collision_artifact_id": "sha256:<COLLISION_MESH_ID>",
  "frame_id": "asset_canonical",
  "unit": "meter",
  "body_type": "dynamic",
  "mass_kg": 12.5,
  "center_of_mass_m": [0.0, 0.0, 0.1],
  "inertia_kg_m2": [[1.0, 0.0, 0.0], [0.0, 1.2, 0.0], [0.0, 0.0, 1.4]],
  "static_friction": 0.7,
  "dynamic_friction": 0.5,
  "restitution": 0.1,
  "source": "user",
  "provided_by": "operator-id"
}
```

Run:

```bash
assets-generator apply-rigid-body \
  --release '<ASSET_RELEASE_ARTIFACT_ID_OR_REFERENCE_JSON>' \
  --properties rigid-body.json \
  --store '<STORE>' \
  --output '<NEW_RELEASE>'
```

The command preserves visual and collision bytes and publishes a new AssetDefinition/AssetRelease
with typed physics, evidence, QA, provenance, and BuildRun records. The caller remains responsible
for the physical accuracy of every supplied value.

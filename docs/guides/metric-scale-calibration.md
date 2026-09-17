# Metric Scale Calibration

Create a measurement JSON whose points are expressed in the source asset's canonical coordinates:

```json
{
  "schema_version": "1.0",
  "source_asset_id": "sha256:<ASSET_DEFINITION_ID>",
  "visual_artifact_id": "sha256:<CANONICAL_VISUAL_MESH_ID>",
  "frame_id": "asset_canonical",
  "unit": "relative_unit",
  "point_a": [-0.5, 0.0, 0.0],
  "point_b": [0.5, 0.0, 0.0],
  "distance_meters": 1.8,
  "source": "user-tape-measure"
}
```

Run:

```bash
assets-generator calibrate-scale \
  --release '<ASSET_RELEASE_ARTIFACT_ID_OR_REFERENCE_JSON>' \
  --measurement measurement.json \
  --store '<STORE>' \
  --output '<NEW_RELEASE>'
```

The source release remains unchanged. The new release contains meter-unit visual geometry and, when
present, a collision mesh scaled by the same factor. The command does not infer the measurement or
validate that the selected real-world points correspond to the intended object landmarks.

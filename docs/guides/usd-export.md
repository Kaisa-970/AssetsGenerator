# Standard USD rigid asset export

Install the optional CPU SDK in the existing Core environment:

```bash
python -m pip install -e '.[usd]'
```

Use a release produced by `apply-rigid-body`. It must contain metric canonical visual and convex
collision geometry, explicit dynamic rigid-body properties, and their original evidence and
successful BuildRun records in the same Artifact Store.

```bash
assets-generator export-usd \
  --release '<RIGID_ASSET_RELEASE_ARTIFACT_ID_OR_REFERENCE_JSON>' \
  --store '<STORE>' \
  --output '<NEW_RELEASE>'
```

The new release preserves the source AssetDefinition and source files. Its standard USD entry is
`geometry/usd/asset.usda`; referenced textures are packaged alongside it. QA, SDK/environment
identity, provenance, and a BuildRun accompany the export. The output directory must be new.

The first profile supports opaque visuals with vertex/face colors or per-geometry PBR factors and
base-color texture/UV bindings. Unsupported material, animation, or glTF extension features fail
explicitly. Geometry remains in canonical Z-up meter coordinates. Collision uses the supplied
convex proxy and the standard `convexHull` approximation token.

SDK roundtrip verification does not establish Isaac Sim compatibility, simulation stability, or
the accuracy of supplied physical parameters. Scene export, articulation, automatic physics
estimation, and runtime synchronization remain outside this profile.

Collision geometry uses USD `purpose=guide`, separating it from the normal visual rendering path.

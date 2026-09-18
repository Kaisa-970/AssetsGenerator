# Standard USD rigid export v1

Status: initial implementation, 2026-09-18. Standard SDK roundtrip is supported; Isaac runtime validation remains pending.

This Phase 8 slice exports one metric rigid asset using the standard OpenUSD SDK and
UsdPhysics schemas. Preserve the source AssetDefinition and create a new AssetRelease. An SDK
roundtrip proves that authored data can be parsed; it does not prove Isaac Sim compatibility or
physical accuracy.

## Representation

- Author stage metadata `upAxis=Z`, `metersPerUnit=1`, `kilogramsPerUnit=1`, and a default root Xform.
  Use canonical geometry directly; do not apply the GLB export axis permutation again.
- Bind RigidBodyAPI and MassAPI to the root. Copy the supplied mass and canonical center of mass.
  Decompose the symmetric inertia tensor into diagonal principal moments and a right-handed
  principal-axis rotation. Define deterministic eigenvector signs, ordering, repeated-eigenvalue
  behavior and quaternion sign. Reload and reconstruct the tensor to validate the conversion.
- Put visual meshes below a visual child and collision below a separate collision child. Bind
  CollisionAPI and MeshCollisionAPI only to collision geometry. Define `convexHull` explicitly for
  the current convex proxy; simulator cooking may still change its effective collision shape.
- Author a physics MaterialAPI with supplied static/dynamic friction and restitution, and bind
  it for physics purposes. Do not author density or engine-specific combine modes that would
  override or reinterpret supplied values silently.
- Preserve geometry instances/transforms, vertex colors and supported PBR texture/UV bindings.
  Unsupported material features must fail explicitly; a successful export must not silently
  replace textured assets with plain geometry. Keep all referenced textures inside the release.

## Execution and acceptance

OpenUSD is the optional `usd` extra (`usd-core==26.5`), imported only by the exporter. It adds no model
or CUDA dependency. Provenance records the Python executable, SDK installed-content digest, writer
digest, and `openusd-rigid-v1` profile. The SDK was installed in the existing Core environment.

Validate source physics evidence, geometry digests, frames and meter units before export. Emit
BuildRun attempts for export with USD roundtrip QA, release derivation and atomic no-replace publication.
The source AssetDefinition remains the semantic authority; USD is an export artifact. Preserve all
source release evidence and add direct provenance for USD, QA and the derived release.

Tests cover off-diagonal and repeated-eigenvalue inertia, spatial roundtrips, texture and
vertex-color preservation, collision/visual separation, package-local references, corrupt evidence,
relative-scale rejection and publication failure. A CPU roundtrip uses an existing real generated
asset. Keep Isaac-specific profile naming and runtime readiness pending actual
Isaac import and simulation tests; articulation and scene physics synchronization remain deferred.

## References inspected

- [UsdPhysics MassAPI](https://openusd.org/release/api/class_usd_physics_mass_a_p_i.html):
  centerOfMass, diagonalInertia, principalAxes and mass-unit semantics.
- [UsdPhysics MaterialAPI](https://openusd.org/release/api/class_usd_physics_material_a_p_i.html):
  static/dynamic friction and restitution.
- [UsdPhysics MeshCollisionAPI](https://openusd.org/release/api/class_usd_physics_mesh_collision_a_p_i.html):
  explicit approximation tokens including convexHull.

## Current supported appearance

Opaque per-geometry PBR base-color factors/textures, UVs, metallic/roughness factors, double-sided
settings, and opaque vertex/face colors are supported. Normal, occlusion, emissive and metallic-
roughness textures, transparent materials, glTF extensions, custom samplers, animation, skinning,
and combined vertex-color/material inputs are rejected before Trimesh can discard information.
USD files use canonical geometry, while the original GLB and evidence stay in the release.

See [usage](../guides/usd-export.md) and [CPU smoke](../reports/phase8-usd-smoke-v1.md).

Collision geometry uses USD `purpose=guide`, separating it from the normal visual rendering path.

USD mass and principal moments use float32 attributes. Values that cannot remain finite and
strictly positive at that precision are rejected instead of becoming zero or inferred properties.
Explicit source normals are preserved; absent GLB normals use flat face-varying normals.

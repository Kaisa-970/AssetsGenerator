# USD export boundary (proposed)

Status: design proposal, 2026-09-18. No USD exporter or Isaac runtime validation is implemented.

The next Phase 8 slice should export one metric rigid asset using the standard OpenUSD SDK and
UsdPhysics schemas. Preserve the source AssetDefinition and create a new AssetRelease. An SDK
roundtrip proves that authored data can be parsed; it does not prove Isaac Sim compatibility or
physical accuracy.

## Proposed representation

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

Make OpenUSD an optional dependency or isolated export environment; it must not pull model or CUDA
dependencies into Core. Record SDK/environment identity and export profile version in provenance.
The current Core environment and existing conda environments were checked on 2026-09-18 and contain
no `pxr` module; no SDK installation or USD execution is claimed by this proposal.

Validate source physics evidence, geometry digests, frames and meter units before export. Emit
BuildRun attempts for export, USD roundtrip QA, release derivation and atomic no-replace publication.
The source AssetDefinition remains the semantic authority; USD is an export artifact. Preserve all
source release evidence and add direct provenance for USD, QA and the derived release.

Required tests include off-diagonal and repeated-eigenvalue inertia, spatial roundtrips, texture and
vertex-color preservation, collision/visual separation, package-local references, corrupt evidence,
relative-scale rejection and publication failure. Run a CPU roundtrip on an existing real generated
asset after SDK setup. Keep Isaac-specific profile naming and runtime readiness pending actual
Isaac import and simulation tests; articulation and scene physics synchronization remain deferred.

## References inspected

- [UsdPhysics MassAPI](https://openusd.org/release/api/class_usd_physics_mass_a_p_i.html):
  centerOfMass, diagonalInertia, principalAxes and mass-unit semantics.
- [UsdPhysics MaterialAPI](https://openusd.org/release/api/class_usd_physics_material_a_p_i.html):
  static/dynamic friction and restitution.
- [UsdPhysics MeshCollisionAPI](https://openusd.org/release/api/class_usd_physics_mesh_collision_a_p_i.html):
  explicit approximation tokens including convexHull.

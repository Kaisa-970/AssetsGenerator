# Explicit Rigid Body Properties v1

Phase 8 v1 provides an explicitly requested postprocess for one dynamic rigid body. The source
asset must already use `meter`, have `scale_status=metric`, contain exactly one canonical visual and
one canonical collision mesh, and have `physics=null`. Core does not estimate, default, clamp, or
repair physical parameters.

`RigidBodyProperties@1.0` binds the caller's values to the exact source AssetRelease,
AssetDefinition, collision Artifact, canonical frame, and meter unit. V1 requires positive mass, a
three-value center of mass, and a symmetric positive-definite 3x3 inertia tensor expressed in the
canonical axes about that center of mass. Principal moments must satisfy the rigid-body triangle
inequality. Static and dynamic friction are nonnegative, dynamic friction cannot exceed static
friction, and restitution is in `[0, 1]`. Friction values above one remain valid.

Tensor symmetry uses a tolerance equal to the larger of `1e-12` times the largest absolute entry
and 16 floating-point spacings at that magnitude. Principal-moment tests use the same rule at the
largest absolute eigenvalue; the minimum eigenvalue must exceed that tolerance. This excludes
numerically singular tensors without applying an absolute inertia floor. Signed zeros normalize
to positive zero. The supplied tensor is not symmetrized or repaired.

The derived `PhysicsInfo@1.0` retains the exact properties evidence Artifact, collision Artifact,
provider identity, frame, unit, and normalized SI values. QA verifies evidence binding, spatial and
collision contracts, and mathematical consistency only. It does not validate that the supplied
values describe the real object or behave equivalently in every simulator.

The source release and geometry are immutable. Publication reuses every existing file ArtifactRef
and adds properties evidence, QA, and provenance. The complete release, including a succeeded
BuildRun, is exposed through one final directory rename; failure leaves no output directory and
persists a failed BuildRun. Static/kinematic bodies, compound collision, density inference,
damping, simulator-specific fields, USD/IsaacUSD, runtime synchronization, and articulation are
deferred.

Current no-replace publication requires Linux `renameat2(RENAME_NOREPLACE)` support and fails
explicitly if it is unavailable. Existing output directories, including empty directories created
concurrently, must never be replaced.

Source validation checks canonical visual and collision Artifact integrity, metric frames, and
canonical-to-glTF geometry correspondence. Collision export evidence must reference a succeeded
BuildRun and matching output. Successful properties import retains provenance even if later
assignment fails.

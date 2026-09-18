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

# Metric Scale Calibration v1

Phase 8 v1 supports an explicit point-distance measurement. The caller binds it to the exact source
AssetDefinition, canonical visual Artifact, frame and `relative_unit`, then supplies two points and
their measured real distance in meters. Core persists
the evidence as `MetricScaleMeasurement@1.0`, computes one positive uniform scale, and applies it to
every canonical visual and collision mesh.

The workflow rejects inferred or ambiguous scale, non-finite or coincident points, non-positive
distance, assets already marked metric, unsupported OBB state, multiple visual/collision meshes,
non-null physics, and spatial metadata inconsistent with the AssetDefinition. Point clouds and
Gaussians are rejected until they have a defined scaling path. It does not estimate scale from RGB,
semantics, category priors, camera metadata, or model output.

The result is a new immutable AssetDefinition and AssetRelease. It changes `unit` to `meter`,
`scale_status` to `metric`, recomputes the AABB, remaps component provenance to the scaled visual
mesh, regenerates visual/collision GLBs, and records direct provenance for scaled geometry, QA, and
the derived asset. Physics remains unchanged and no mass or material property is inferred.

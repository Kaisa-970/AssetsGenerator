from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .models import AABB, AssetSpatialInfo, BackendNativeFrame, Frame, SpatialTransform

CANONICALIZATION_RULE_VERSION = "phase1-v1"
AXIS_TIE_EPSILON = 1e-8


class SpatialContractError(ValueError):
    pass


def validate_backend_native_frame(frame: BackendNativeFrame) -> None:
    if not isinstance(frame.frame_id, str) or not frame.frame_id:
        raise SpatialContractError("BackendNativeFrame requires frame_id")
    if not isinstance(frame.handedness, str) or frame.handedness not in {"right", "left"}:
        raise SpatialContractError(f"invalid handedness: {frame.handedness}")
    axes = {"+X", "-X", "+Y", "-Y", "+Z", "-Z"}
    if not isinstance(frame.up_axis, str) or frame.up_axis not in axes:
        raise SpatialContractError(f"invalid up axis: {frame.up_axis}")
    if frame.forward_axis is not None:
        if not isinstance(frame.forward_axis, str) or frame.forward_axis not in axes:
            raise SpatialContractError(f"invalid forward axis: {frame.forward_axis}")
        if abs(float(_axis_vector(frame.forward_axis) @ _axis_vector(frame.up_axis))) > 0.0:
            raise SpatialContractError("up and forward axes must be orthogonal")
    if not isinstance(frame.forward_status, str) or frame.forward_status not in {
        "declared",
        "estimated",
        "unknown",
    }:
        raise SpatialContractError(f"invalid forward status: {frame.forward_status}")
    if not isinstance(frame.unit, str) or frame.unit not in {"meter", "relative_unit"}:
        raise SpatialContractError(f"invalid native frame unit: {frame.unit}")
    if frame.forward_axis is None and frame.forward_status != "unknown":
        raise SpatialContractError("missing forward axis requires unknown forward status")
    if frame.forward_axis is not None and frame.forward_status == "unknown":
        raise SpatialContractError("known forward axis requires declared or estimated status")


def validate_mesh_native_frame(
    identity_metadata: Mapping[str, Any], frame: BackendNativeFrame
) -> None:
    validate_backend_native_frame(frame)
    if identity_metadata.get("frame_id") != frame.frame_id:
        raise SpatialContractError("mesh frame_id does not match BackendNativeFrame")
    if identity_metadata.get("unit") != frame.unit:
        raise SpatialContractError("mesh unit does not match BackendNativeFrame")
    if (
        identity_metadata.get("up_axis") is not None
        and identity_metadata["up_axis"] != frame.up_axis
    ):
        raise SpatialContractError("mesh up_axis does not match BackendNativeFrame")
    if (
        identity_metadata.get("forward_axis") is not None
        and identity_metadata["forward_axis"] != frame.forward_axis
    ):
        raise SpatialContractError("mesh forward_axis does not match BackendNativeFrame")


@dataclass(frozen=True)
class CanonicalizationResult:
    vertices: NDArray[np.float64]
    frame: Frame
    transform: SpatialTransform
    spatial_info: AssetSpatialInfo
    rule_parameters: dict[str, object]


def _axis_vector(axis: str) -> NDArray[np.float64]:
    sign = -1.0 if axis.startswith("-") else 1.0
    name = axis[-1].upper()
    if name not in "XYZ":
        raise SpatialContractError(f"invalid axis: {axis}")
    result = np.zeros(3, dtype=np.float64)
    result["XYZ".index(name)] = sign
    return result


def _basis_from_frame(frame: BackendNativeFrame) -> NDArray[np.float64]:
    up = _axis_vector(frame.up_axis)
    if frame.forward_axis is None:
        candidates = [
            _axis_vector("+X"),
            _axis_vector("+Y"),
            _axis_vector("+Z"),
        ]
        forward = next(candidate for candidate in candidates if abs(float(candidate @ up)) < 0.5)
    else:
        forward = _axis_vector(frame.forward_axis)
    if abs(float(forward @ up)) > AXIS_TIE_EPSILON:
        raise SpatialContractError("up and forward axes must be orthogonal")
    if frame.handedness == "right":
        left = np.cross(up, forward)
    else:
        left = -np.cross(up, forward)
    return np.column_stack((forward, left, up))


def _homogeneous(
    linear: NDArray[np.float64], translation: NDArray[np.float64]
) -> NDArray[np.float64]:
    result = np.eye(4, dtype=np.float64)
    result[:3, :3] = linear
    result[:3, 3] = translation
    return result


def _apply(vertices: NDArray[np.float64], matrix: NDArray[np.float64]) -> NDArray[np.float64]:
    homogeneous = np.column_stack((vertices, np.ones(len(vertices), dtype=np.float64)))
    return (matrix @ homogeneous.T).T[:, :3]


def canonicalize_vertices(
    vertices: NDArray[np.float64],
    native_frame: BackendNativeFrame,
) -> CanonicalizationResult:
    validate_backend_native_frame(native_frame)
    points = np.asarray(vertices, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) == 0:
        raise SpatialContractError("vertices must be a non-empty Nx3 array")
    if not np.isfinite(points).all():
        raise SpatialContractError("vertices contain non-finite coordinates")

    native_basis = _basis_from_frame(native_frame)
    up_aligned = points @ native_basis
    forward_status = native_frame.forward_status
    yaw = np.eye(3, dtype=np.float64)
    if native_frame.forward_axis is None:
        extents = np.ptp(up_aligned, axis=0)
        if abs(float(extents[0] - extents[1])) > AXIS_TIE_EPSILON:
            longest = 0 if extents[0] > extents[1] else 1
            if longest == 1:
                yaw = np.array([[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
            forward_status = "estimated"
        else:
            forward_status = "unknown"
    oriented = up_aligned @ yaw.T

    horizontal = oriented[:, :2]
    forward_component = horizontal[:, 0]
    dominant_index = int(np.argmax(np.abs(forward_component)))
    yaw_flip_applied = False
    if forward_component[dominant_index] < 0:
        yaw_flip = np.diag([-1.0, -1.0, 1.0])
        yaw = yaw_flip @ yaw
        oriented = up_aligned @ yaw.T
        yaw_flip_applied = True

    minimum = oriented.min(axis=0)
    maximum = oriented.max(axis=0)
    center_floor = np.array(
        [(minimum[0] + maximum[0]) / 2, (minimum[1] + maximum[1]) / 2, minimum[2]]
    )
    extent = maximum - minimum
    largest_extent = float(np.max(extent))
    if largest_extent <= AXIS_TIE_EPSILON:
        raise SpatialContractError("mesh bounding box is degenerate")
    metric = native_frame.unit == "meter"
    scale = 1.0 if metric else 1.0 / largest_extent
    linear = scale * yaw @ native_basis.T
    translation = -scale * center_floor
    matrix = _homogeneous(linear, translation)
    canonical = _apply(points, matrix)
    canonical_min = canonical.min(axis=0)
    canonical_max = canonical.max(axis=0)

    frame = Frame(
        "asset_canonical", "asset", "right", "+Z", "+X", "meter" if metric else "relative_unit"
    )
    transform = SpatialTransform(native_frame.frame_id, frame.frame_id, matrix.tolist())
    spatial_info = AssetSpatialInfo(
        canonical_frame_id=frame.frame_id,
        aabb=AABB(canonical_min.tolist(), canonical_max.tolist()),
        obb=None,
        scale_status="metric" if metric else "relative",
        unit=frame.unit,
        forward_status=forward_status,
    )
    return CanonicalizationResult(
        canonical,
        frame,
        transform,
        spatial_info,
        {
            "rule_version": CANONICALIZATION_RULE_VERSION,
            "axis_tie_epsilon": AXIS_TIE_EPSILON,
            "yaw_tie_break": "dominant_native_component_positive",
            "yaw_flip_applied": yaw_flip_applied,
            "origin_rule": "aabb_floor_center",
            "relative_scale_rule": "largest_aabb_extent_equals_one",
        },
    )

from __future__ import annotations

import numpy as np
import pytest

from assets_generator.models import BackendNativeFrame
from assets_generator.spatial import SpatialContractError, canonicalize_vertices


def test_canonicalization_sets_floor_center_origin_and_relative_scale() -> None:
    vertices = np.array(
        [
            [-1.0, -2.0, -3.0],
            [1.0, -2.0, -3.0],
            [-1.0, 2.0, 3.0],
            [1.0, 2.0, 3.0],
        ]
    )
    native = BackendNativeFrame("native", "right", "+Z", "+X", "declared", "relative_unit")
    result = canonicalize_vertices(vertices, native)

    assert result.frame.up_axis == "+Z"
    assert result.frame.forward_axis == "+X"
    assert result.spatial_info.unit == "relative_unit"
    assert result.spatial_info.scale_status == "relative"
    assert np.allclose(result.vertices.min(axis=0)[2], 0.0)
    assert np.allclose((result.vertices.min(axis=0)[:2] + result.vertices.max(axis=0)[:2]) / 2, 0.0)
    assert np.isclose(np.ptp(result.vertices, axis=0).max(), 1.0)


def test_y_up_native_frame_is_rotated_to_z_up() -> None:
    vertices = np.array([[0.0, 0.0, 0.0], [0.0, 2.0, 0.0], [1.0, 0.0, 0.0]])
    native = BackendNativeFrame("native", "right", "+Y", "+Z", "declared", "relative_unit")
    result = canonicalize_vertices(vertices, native)

    native_up_direction = result.vertices[1] - result.vertices[0]
    assert np.allclose(native_up_direction[:2], 0.0)
    assert native_up_direction[2] > 0
    assert result.transform.source_frame_id == "native"
    assert result.transform.target_frame_id == "asset_canonical"


def test_unknown_forward_uses_longest_horizontal_extent() -> None:
    vertices = np.array([[-1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 4.0, 1.0]])
    native = BackendNativeFrame("native", "right", "+Z", None, "unknown", "relative_unit")
    result = canonicalize_vertices(vertices, native)

    extents = np.ptp(result.vertices, axis=0)
    assert extents[0] >= extents[1]
    assert result.spatial_info.forward_status == "estimated"


def test_canonicalization_rejects_unknown_native_unit() -> None:
    native = BackendNativeFrame("native", "right", "+Z", None, "unknown", "millimeter")

    with pytest.raises(SpatialContractError, match="invalid native frame unit"):
        canonicalize_vertices(np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]), native)


@pytest.mark.parametrize(
    ("native", "message"),
    [
        (
            BackendNativeFrame("native", "right", "+Z", None, "declared", "relative_unit"),
            "missing forward axis requires unknown",
        ),
        (
            BackendNativeFrame("native", "right", "+Z", "+X", "unknown", "relative_unit"),
            "known forward axis requires",
        ),
        (
            BackendNativeFrame(123, "right", "+Z", None, "unknown", "relative_unit"),  # type: ignore[arg-type]
            "requires frame_id",
        ),
    ],
)
def test_canonicalization_rejects_inconsistent_native_frame(native, message) -> None:
    with pytest.raises(SpatialContractError, match=message):
        canonicalize_vertices(np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]), native)


@pytest.mark.parametrize("up", ["+X", "-X", "+Y", "-Y", "+Z", "-Z"])
@pytest.mark.parametrize("forward", ["+X", "-X", "+Y", "-Y", "+Z", "-Z"])
def test_native_frame_axis_pairs(up, forward) -> None:
    from assets_generator.spatial import validate_backend_native_frame

    frame = BackendNativeFrame("native", "right", up, forward, "declared", "relative_unit")
    if up[-1] == forward[-1]:
        with pytest.raises(SpatialContractError, match="orthogonal"):
            validate_backend_native_frame(frame)
    else:
        validate_backend_native_frame(frame)

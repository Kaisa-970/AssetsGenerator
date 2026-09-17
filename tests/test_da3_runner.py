from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from assets_generator.backends.da3_runner import (
    backproject,
    camera_to_world,
    prediction_arrays,
    preprocessing_geometry,
    restore_depth,
    restore_intrinsics,
    write_points,
)


def test_resize_rounding_and_batch_crop_restore_intrinsics():
    geometries = preprocessing_geometry([(1200, 800), (800, 1200)], 392)
    assert geometries[0]["resized_size"] == [392, 266]
    assert geometries[0]["processed_size"] == [266, 266]
    assert geometries[0]["crop_left"] == 63
    original = np.array([[900.0, 0, 601], [0, 850, 399], [0, 0, 1]])
    for geo in geometries:
        processed = original.copy()
        processed[0] *= geo["scale_x"]
        processed[1] *= geo["scale_y"]
        processed[0, 2] -= geo["crop_left"]
        processed[1, 2] -= geo["crop_top"]
        np.testing.assert_allclose(restore_intrinsics(processed, geo), original)


def test_resize_tie_rounds_up_and_invalid_shapes_fail():
    assert preprocessing_geometry([(28, 21)], 28)[0]["resized_size"] == [28, 28]
    with pytest.raises(ValueError):
        preprocessing_geometry([(10000, 1)], 392)
    with pytest.raises(ValueError):
        preprocessing_geometry([], 392)


def test_depth_original_grid_rejects_invalid_and_cropped_pixels(tmp_path):
    geo = preprocessing_geometry([(56, 28), (28, 56)], 56)[0]
    depth = np.ones((28, 28), dtype=np.float32) * 2
    confidence = np.ones_like(depth)
    depth[1, 1] = np.nan
    depth[2, 2] = -1
    confidence[3, 3] = 0.1
    result = restore_depth(depth, confidence, geo, 0.5)
    assert result.shape == (28, 56)
    assert np.all(result[:, :14] == 0)
    assert np.all(result[:, 42:] == 0)
    assert result[1, 15] == result[2, 16] == result[3, 17] == 0
    assert result[0, 14] == 2
    path = tmp_path / "depth.tiff"
    Image.fromarray(result).save(path)
    with Image.open(path) as image:
        np.testing.assert_array_equal(np.asarray(image), result)
    with pytest.raises(ValueError, match="shape"):
        restore_depth(depth[:, :-1], confidence, geo, 0.5)


def test_w2c_inverse_and_backprojection_reproject_consistently(tmp_path):
    rotation = np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]])
    w2c = np.column_stack([rotation, [1, 2, 3]])
    transform = camera_to_world(w2c)
    homogeneous = np.vstack([w2c, [0, 0, 0, 1]])
    np.testing.assert_allclose(homogeneous @ transform, np.eye(4), atol=1e-12)
    depth = np.array([[2, 0], [np.nan, 4]])
    intrinsics = np.array([[2, 0, 0.5], [0, 2, 0.5], [0, 0, 1]])
    points = backproject(depth, intrinsics, transform)
    camera = points @ rotation.T + w2c[:, 3]
    projected = camera @ intrinsics.T
    np.testing.assert_allclose(projected[:, :2] / projected[:, 2:], [[0, 0], [1, 1]])
    np.testing.assert_allclose(camera[:, 2], [2, 4])
    path = tmp_path / "points.ply"
    write_points(path, points)
    import trimesh

    loaded = trimesh.load(path, process=False)
    np.testing.assert_allclose(loaded.vertices, points)
    with pytest.raises(ValueError, match="rigid"):
        camera_to_world(np.diag([2, 1, 1, 1]))
    with pytest.raises(ValueError, match="rigid"):
        camera_to_world(np.diag([-1, 1, 1, 1]))


def test_prediction_requires_camera_and_confidence_for_every_view():
    prediction = SimpleNamespace(
        depth=np.ones((2, 14, 14)),
        conf=np.ones((2, 14, 14)),
        intrinsics=np.repeat(np.eye(3)[None], 2, axis=0),
        extrinsics=np.repeat(np.eye(4)[None], 2, axis=0),
    )
    assert prediction_arrays(prediction, 2)[0].shape == (2, 14, 14)
    prediction.extrinsics = np.eye(4)[None]
    with pytest.raises(ValueError, match="camera count"):
        prediction_arrays(prediction, 2)
    prediction.conf = None
    with pytest.raises(ValueError, match="confidence"):
        prediction_arrays(prediction, 2)

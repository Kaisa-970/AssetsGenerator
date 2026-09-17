from types import SimpleNamespace

import numpy as np
import pytest
import trimesh
from PIL import Image

from assets_generator.backends import open3d_runner as runner


def test_camera_inverts_column_vector_transform():
    camera_world = np.array([[0, 0, 1, 2], [0, 1, 0, 3], [-1, 0, 0, 4], [0, 0, 0, 1]])
    k = [[50, 0, 3], [0, 60, 4], [0, 0, 1]]
    matrix, world_camera = runner.camera_parameters(k, camera_world)
    np.testing.assert_allclose(matrix, k)
    np.testing.assert_allclose(world_camera @ camera_world, np.eye(4))
    np.testing.assert_allclose(world_camera @ [2, 3, 4, 1], [0, 0, 0, 1])


@pytest.mark.parametrize(
    "k",
    [
        [[50, 1, 3], [0, 60, 4], [0, 0, 1]],
        [[-50, 0, 3], [0, 60, 4], [0, 0, 1]],
        [[50, 0, 3], [0, 60, 4], [0, 1, 1]],
        [[np.nan, 0, 3], [0, 60, 4], [0, 0, 1]],
    ],
)
def test_camera_rejects_unsupported_intrinsics(k):
    with pytest.raises(ValueError, match="intrinsics"):
        runner.camera_parameters(k, np.eye(4))


@pytest.mark.parametrize("transform", [np.diag([2, 1, 1, 1]), np.diag([-1, 1, 1, 1])])
def test_camera_rejects_nonrigid_and_left_handed_transform(transform):
    with pytest.raises(ValueError, match="rigid"):
        runner.camera_parameters(np.eye(3), transform)


def test_masks_and_invalid_values_are_removed_before_reference_median():
    depth = runner.prepare_depth(
        np.array([[1, 3, 100], [7, 999, -1]]),
        np.array([[255, 255, 0], [255, 255, 255]]),
        999,
    )
    np.testing.assert_array_equal(depth, [[1, 3, 0], [7, 0, 0]])
    assert depth.dtype == np.float32 and depth.flags.c_contiguous
    scale = runner.fusion_scale([depth], {})
    assert scale["reference_depth"] == 3
    assert scale["voxel_length"] == pytest.approx(0.03)
    assert scale["sdf_trunc"] == pytest.approx(0.12)
    assert scale["depth_trunc"] == 9
    # All views contribute pixels, not equally weighted per-view medians.
    assert runner.fusion_scale([depth, np.array([[20, 30]])], {})["reference_depth"] == 7


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "1"])
def test_invalid_fusion_ratios_rejected(value):
    with pytest.raises(ValueError, match="positive"):
        runner.fusion_scale([np.ones((1, 1))], {"voxel_size_ratio": value})


def test_empty_depth_and_malformed_mask_fail():
    with pytest.raises(ValueError, match="no valid"):
        runner.fusion_scale([np.zeros((1, 1))], {})
    with pytest.raises(ValueError, match="finite"):
        runner.prepare_depth(np.array([[np.nan]]), np.array([[0]]), 0)
    with pytest.raises(ValueError, match="mask"):
        runner.prepare_depth(np.ones((1, 1)), np.array([[1]]), 0)
    with pytest.raises(ValueError, match="at least"):
        runner.fusion_scale([np.ones((1, 1))], {"sdf_trunc_ratio": 0.001})


def mock_open3d(monkeypatch, *, empty=False):
    calls = SimpleNamespace(integrations=[], images=[])

    class Volume:
        def __init__(self, **kwargs):
            calls.volume = kwargs

        def integrate(self, rgbd, intrinsic, extrinsic):
            calls.integrations.append((rgbd, intrinsic, extrinsic))

        def extract_triangle_mesh(self):
            return SimpleNamespace(
                vertices=np.array([[0, 0, 1], [1, 0, 1], [0, 1, 1]]),
                triangles=np.empty((0, 3), dtype=int) if empty else np.array([[0, 1, 2]]),
                vertex_colors=np.array([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]),
            )

    def rgbd(color, depth, **kwargs):
        calls.images.append((color, depth, kwargs))
        return SimpleNamespace(color=color, depth=depth)

    o3d = SimpleNamespace(
        __version__="0.19.0",
        pipelines=SimpleNamespace(
            integration=SimpleNamespace(
                ScalableTSDFVolume=Volume,
                TSDFVolumeColorType=SimpleNamespace(RGB8="RGB8"),
            )
        ),
        geometry=SimpleNamespace(
            Image=lambda array: array,
            RGBDImage=SimpleNamespace(create_from_color_and_depth=rgbd),
        ),
        camera=SimpleNamespace(PinholeCameraIntrinsic=lambda *args: args),
    )
    real_import = runner.importlib.import_module
    monkeypatch.setattr(
        runner.importlib,
        "import_module",
        lambda name: o3d if name == "open3d" else real_import(name),
    )
    return calls


def request_fixture(tmp_path):
    image = tmp_path / "rgb.png"
    depth = tmp_path / "depth.tiff"
    Image.new("RGB", (2, 2), (12, 34, 56)).save(image)
    Image.fromarray(np.array([[1, 2], [3, 4]], dtype=np.float32)).save(depth)
    transform = np.eye(4)
    transform[:3, 3] = [1, 2, 3]
    return {
        "views": [
            {
                "view_id": "front",
                "image": str(image),
                "depth": str(depth),
                "intrinsics": [[5, 0, 1], [0, 5, 1], [0, 0, 1]],
                "T_world_camera": transform.tolist(),
                "invalid_value": 0,
            }
        ],
        "output_dir": str(tmp_path / "output"),
        "world_frame": "world",
        "unit": "relative_unit",
    }


def test_run_integrates_rgb_float_depth_inverse_pose_and_exports_vertex_colors(
    tmp_path, monkeypatch
):
    calls = mock_open3d(monkeypatch)
    result = runner.run(request_fixture(tmp_path))
    _, intrinsic, extrinsic = calls.integrations[0]
    np.testing.assert_allclose(extrinsic[:3, 3], [-1, -2, -3])
    assert intrinsic[:2] == (2, 2)
    rgb, depth, options = calls.images[0]
    np.testing.assert_array_equal(rgb[0, 0], [12, 34, 56])
    np.testing.assert_array_equal(depth, [[1, 2], [3, 4]])
    assert options == {"depth_scale": 1.0, "depth_trunc": 7.5, "convert_rgb_to_intensity": False}
    scene = trimesh.load(result["mesh"], process=False)
    mesh = next(iter(scene.geometry.values()))
    assert mesh.visual.kind == "vertex"
    np.testing.assert_array_equal(mesh.visual.vertex_colors[:, :3], np.eye(3, dtype=np.uint8) * 255)
    metadata = result["backend_metadata"]
    assert metadata["reference_depth"] == 2.5
    assert metadata["vertex_count"] == 3 and metadata["face_count"] == 1
    assert metadata["appearance_mode"] == "preserve_mesh"
    assert metadata["world_frame"] == "world" and metadata["unit"] == "relative_unit"


def test_run_rejects_empty_mesh_before_export(tmp_path, monkeypatch):
    mock_open3d(monkeypatch, empty=True)
    with pytest.raises(ValueError, match="empty"):
        runner.run(request_fixture(tmp_path))
    assert not (tmp_path / "output" / "visual.glb").exists()


def test_run_rejects_all_depth_truncated(tmp_path, monkeypatch):
    calls = mock_open3d(monkeypatch)
    request = request_fixture(tmp_path)
    request["depth_trunc_ratio"] = 0.1
    with pytest.raises(ValueError, match="no depth pixels"):
        runner.run(request)
    assert not calls.integrations


def test_run_masks_foreground_before_scaling_and_integration(tmp_path, monkeypatch):
    calls = mock_open3d(monkeypatch)
    request = request_fixture(tmp_path)
    mask = tmp_path / "mask.png"
    Image.fromarray(np.array([[255, 0], [0, 0]], dtype=np.uint8)).save(mask)
    request["views"][0]["mask"] = str(mask)
    result = runner.run(request)
    assert result["backend_metadata"]["reference_depth"] == 1
    np.testing.assert_array_equal(calls.images[0][1], [[1, 0], [0, 0]])
    assert result["backend_metadata"]["view_depth_counts"] == [
        {"view_id": "front", "valid_depth_pixels": 1}
    ]


def test_script_directory_does_not_shadow_open3d_package(tmp_path) -> None:
    import os
    import subprocess
    import sys
    from pathlib import Path

    from assets_generator.backends import open3d_runner

    package = tmp_path / "open3d"
    package.mkdir()
    (package / "__init__.py").write_text("__version__ = 'stub-external-package'\n")
    environment = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-c", "import open3d; print(open3d.__version__)"],
        cwd=Path(open3d_runner.__file__).parent,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "stub-external-package"

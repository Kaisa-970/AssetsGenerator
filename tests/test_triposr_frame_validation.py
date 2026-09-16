from __future__ import annotations

import numpy as np
import pytest
import trimesh

from assets_generator.backends.triposr_frame_validation import validate_round_trip


def _fixture() -> tuple[dict, trimesh.Scene]:
    markers = [
        {"name": "x", "center": [0.72, 0.22, 0.24], "radius": 0.105},
        {"name": "y", "center": [0.24, 0.68, 0.28], "radius": 0.080},
        {"name": "z", "center": [0.28, 0.30, 0.76], "radius": 0.060},
    ]
    meshes = []
    for marker in markers:
        mesh = trimesh.creation.icosphere(radius=marker["radius"], subdivisions=2)
        mesh.apply_translation(marker["center"])
        meshes.append(mesh)
    combined = trimesh.util.concatenate(meshes)
    vertices = np.asarray(combined.vertices)
    centers = np.asarray([marker["center"] for marker in markers])
    assignments = np.linalg.norm(vertices[:, None, :] - centers[None, :, :], axis=2).argmin(axis=1)
    source = {
        "markers": markers,
        "marker_geometry": [
            {
                "name": marker["name"],
                "centroid": vertices[assignments == index].mean(axis=0).tolist(),
                "extents": np.ptp(vertices[assignments == index], axis=0).tolist(),
            }
            for index, marker in enumerate(markers)
        ],
        "signed_volume": float(combined.volume),
    }
    return source, trimesh.Scene(combined)


def test_frame_validation_accepts_preserved_axes_and_handedness() -> None:
    source, scene = _fixture()

    report = validate_round_trip(source, scene)

    assert report["status"] == "pass"
    assert report["coordinate_system"] == {"handedness": "right", "up_axis": "+Z"}


def test_frame_validation_rejects_axis_reflection() -> None:
    source, scene = _fixture()
    reflected = scene.copy()
    reflected.apply_transform(np.diag([-1.0, 1.0, 1.0, 1.0]))

    with pytest.raises(ValueError, match="centroids or axis signs"):
        validate_round_trip(source, reflected)


def test_frame_validation_rejects_non_identity_node_transform() -> None:
    source, scene = _fixture()
    node = next(iter(scene.graph.nodes_geometry))
    scene.graph.update(frame_to=node, matrix=np.diag([1.0, 1.0, 1.0, 2.0]))

    with pytest.raises(ValueError, match="non-identity scene node transform"):
        validate_round_trip(source, scene)

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .source_identity import backend_source_identity


def _digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _geometry_summary(
    scene: Any, markers: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], float, dict[str, list[list[float]]]]:
    import numpy as np
    import trimesh

    merged = scene.to_geometry()
    assert isinstance(merged, trimesh.Trimesh)
    vertices = np.asarray(merged.vertices)
    marker_centers = np.asarray([marker["center"] for marker in markers])
    assignments = np.linalg.norm(vertices[:, None, :] - marker_centers[None, :, :], axis=2).argmin(
        axis=1
    )
    if any(not np.any(assignments == index) for index in range(len(markers))):
        raise ValueError("GLB reload changed marker centroids or axis signs")
    transforms = {
        str(node): np.asarray(scene.graph.to_flattened()[node]["transform"]).reshape(4, 4).tolist()
        for node in scene.graph.nodes_geometry
    }
    return (
        [
            {
                "name": marker["name"],
                "centroid": vertices[assignments == index].mean(axis=0).tolist(),
                "extents": np.ptp(vertices[assignments == index], axis=0).tolist(),
            }
            for index, marker in enumerate(markers)
        ],
        float(merged.volume),
        transforms,
    )


def validate_round_trip(source: dict[str, Any], scene: Any) -> dict[str, Any]:
    import numpy as np

    reloaded, reloaded_volume, node_transforms = _geometry_summary(scene, source["markers"])
    identity = np.eye(4)
    if not node_transforms or any(
        not np.allclose(transform, identity, atol=1e-8) for transform in node_transforms.values()
    ):
        raise ValueError("GLB contains a non-identity scene node transform")
    expected = source["marker_geometry"]
    expected_centroids = np.asarray([item["centroid"] for item in expected])
    reloaded_centroids = np.asarray([item["centroid"] for item in reloaded])
    expected_extents = np.asarray([item["extents"] for item in expected])
    reloaded_extents = np.asarray([item["extents"] for item in reloaded])
    if not np.allclose(reloaded_centroids, expected_centroids, atol=1e-6):
        raise ValueError("GLB reload changed marker centroids or axis signs")
    if not np.allclose(reloaded_extents, expected_extents, atol=1e-6):
        raise ValueError("GLB reload changed marker extents")
    expected_volume = float(source["signed_volume"])
    if not np.isclose(reloaded_volume, expected_volume, rtol=1e-6, atol=1e-8):
        raise ValueError("GLB reload changed marker winding or handedness")
    if expected_volume <= 0 or reloaded_volume <= 0:
        raise ValueError("marker mesh does not preserve positive signed volume")
    centers = {item["name"]: np.asarray(item["center"]) for item in source["markers"]}
    if not (
        centers["x"][0] > centers["y"][0]
        and centers["y"][1] > centers["z"][1]
        and centers["z"][2] > centers["x"][2]
    ):
        raise ValueError("fixture marker centers are not axis-distinguishing")
    return {
        "rule": "triposr-marching-cubes-glb-roundtrip-v1",
        "status": "pass",
        "coordinate_system": {"handedness": "right", "up_axis": "+Z"},
        "node_transforms": node_transforms,
        "marker_geometry": reloaded,
        "signed_volume": reloaded_volume,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate TripoSR native GLB frame semantics")
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=64)
    args = parser.parse_args(argv)
    fixture = Path(__file__).with_name("triposr_frame_fixture.py")
    source_before = backend_source_identity(args.repo)
    with tempfile.TemporaryDirectory(prefix="triposr-frame-") as temporary:
        work = Path(temporary)
        glb = work / "axis-markers.glb"
        source_path = work / "source.json"
        subprocess.run(
            [
                str(args.python.expanduser().absolute()),
                str(fixture),
                "--repo",
                str(args.repo.expanduser().resolve()),
                "--output",
                str(glb),
                "--metadata",
                str(source_path),
                "--resolution",
                str(args.resolution),
            ],
            check=True,
        )
        import trimesh

        source = json.loads(source_path.read_text(encoding="utf-8"))
        source_after = backend_source_identity(args.repo)
        if source_after != source_before:
            raise RuntimeError("TripoSR checkout changed during frame validation")
        scene = trimesh.load(glb, file_type="glb", force="scene")
        validation = validate_round_trip(source, scene)
        report = {
            **validation,
            "backend_python": str(args.python.expanduser().absolute()),
            "triposr_repo": str(args.repo.expanduser().resolve()),
            "triposr_revision": source["triposr_revision"],
            "backend_source": source_before,
            "backend_environment": source["backend_environment"],
            "resolution": source["resolution"],
            "source_metadata_digest": _digest(source_path),
            "fixture_digest": _digest(fixture),
            "validator_digest": _digest(Path(__file__)),
            "glb_digest": _digest(glb),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

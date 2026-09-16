from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from environment_identity import current_environment_identity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a TripoSR native-frame marker GLB")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=64)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(args.repo.expanduser().resolve()))

    import numpy as np
    import torch
    import trimesh
    from tsr.models.isosurface import MarchingCubeHelper

    if not torch.cuda.is_available():
        raise RuntimeError("TripoSR frame validation requires an accessible CUDA GPU")
    markers = [
        {"name": "x", "center": [0.72, 0.22, 0.24], "radius": 0.105},
        {"name": "y", "center": [0.24, 0.68, 0.28], "radius": 0.080},
        {"name": "z", "center": [0.28, 0.30, 0.76], "radius": 0.060},
    ]
    helper = MarchingCubeHelper(args.resolution).cuda()
    points = helper.grid_vertices.cuda()
    fields = []
    for marker in markers:
        center = torch.tensor(marker["center"], device="cuda")
        fields.append(float(marker["radius"]) - torch.linalg.vector_norm(points - center, dim=1))
    level = torch.stack(fields).amax(dim=0)
    vertices, faces = helper(-level)
    mesh = trimesh.Trimesh(
        vertices=vertices.cpu().numpy(),
        faces=faces.cpu().numpy(),
        process=False,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(args.output, file_type="glb")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=args.repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    mesh_vertices = np.asarray(mesh.vertices)
    marker_centers = np.asarray([marker["center"] for marker in markers])
    assignments = np.linalg.norm(
        mesh_vertices[:, None, :] - marker_centers[None, :, :], axis=2
    ).argmin(axis=1)
    args.metadata.write_text(
        json.dumps(
            {
                "triposr_revision": revision,
                "backend_environment": current_environment_identity(),
                "resolution": args.resolution,
                "markers": markers,
                "vertices": int(len(mesh.vertices)),
                "faces": int(len(mesh.faces)),
                "marker_geometry": [
                    {
                        "name": marker["name"],
                        "centroid": mesh_vertices[assignments == index].mean(axis=0).tolist(),
                        "extents": np.ptp(mesh_vertices[assignments == index], axis=0).tolist(),
                    }
                    for index, marker in enumerate(markers)
                ],
                "signed_volume": float(mesh.volume),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

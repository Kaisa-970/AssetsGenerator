from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import trimesh

request_path = Path(sys.argv[1])
response_path = Path(sys.argv[2])
request = json.loads(request_path.read_text(encoding="utf-8"))

vertices = np.array(
    [
        [0.0, 0.0, 0.0],
        [4.0, 0.0, 0.0],
        [0.0, 2.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
)
faces = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])
mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
mesh.visual.vertex_colors = np.array(
    [[255, 0, 0, 255], [0, 255, 0, 255], [0, 0, 255, 255], [255, 255, 255, 255]]
)
mesh.export(request["output_glb"], file_type="glb")
response_path.write_text(
    json.dumps(
        {
            "backend": "triposr",
            "backend_version": "fixture-z-up-export-reload-v1",
            "model": request["model"],
            "model_digest": "sha256:" + "1" * 64,
            "seed": request["seed"],
            "seed_effective": False,
            "pipeline_type": request["pipeline_type"],
            "pipeline_type_effective": False,
            "chunk_size": request["chunk_size"],
            "mc_resolution": request["mc_resolution"],
            "foreground_ratio": request["foreground_ratio"],
            "vertex_count": 4,
            "face_count": 4,
            "peak_cuda_memory_mb": 123.5,
            "backend_environment": request["backend_environment"],
            "backend_source": request["backend_source"],
            "request": request,
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)

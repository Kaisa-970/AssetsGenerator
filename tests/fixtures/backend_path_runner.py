from __future__ import annotations

import json
import sys
from pathlib import Path

import trimesh

request_path = Path(sys.argv[1])
response_path = Path(sys.argv[2])
request = json.loads(request_path.read_text(encoding="utf-8"))
scene = trimesh.Scene(trimesh.creation.box())
scene.export(request["output_glb"])
response_path.write_text(
    json.dumps({"backend_version": "path-test", "request_path": str(request_path)}),
    encoding="utf-8",
)

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

request_path = Path(sys.argv[1])
response_path = Path(sys.argv[2])
request = json.loads(request_path.read_text(encoding="utf-8"))
with Image.open(request["input_image"]) as image:
    Image.new("L", image.size, 255).save(request["output_mask"], format="PNG")
response_path.write_text(
    json.dumps(
        {
            "backend": "birefnet_lite",
            "backend_version": "path-test",
            "model_digest": "sha256:test",
        }
    ),
    encoding="utf-8",
)

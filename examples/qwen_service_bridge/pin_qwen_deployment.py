#!/usr/bin/env python3
"""Generate a content-pinned Qwen deployment manifest without loading a model."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PIL import __version__ as pillow_version
from qwen_generic_bridge import process_identity

parser = argparse.ArgumentParser()
parser.add_argument("--project", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--upstream-pid", type=int, required=True)
args = parser.parse_args()
root = args.project.resolve()
roles = {
    "bridge": str(root / "qwen_generic_bridge.py"),
    "model_executable": str(root / "engine/build/bin/sd-server"),
    "model_launch": str(root / "run-server.sh"),
    "diffusion_weights": str(root / "models/qwen-image-2.1-Q4_K_M.gguf"),
    "text_weights": str(root / "models/Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf"),
    "vision_weights": str(root / "models/mmproj-BF16.gguf"),
    "vae_weights": str(root / "models/qwen_image_2.1_vae_bf16.safetensors"),
}
files = {}
for path in roles.values():
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            h.update(block)
    files[path] = "sha256:" + h.hexdigest()
manifest = {
    "service_id": "qwen-image-2-1",
    "files": files,
    "roles": roles,
    "upstream": "http://127.0.0.1:18080",
    "python": sys.version,
    "pillow": pillow_version,
    "upstream_process": process_identity(args.upstream_pid),
}
args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
print(args.output)

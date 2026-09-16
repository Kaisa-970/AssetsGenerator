from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

if __package__:
    from .environment_identity import current_environment_identity
    from .model_identity import snapshot_digest
    from .source_identity import backend_source_identity
else:
    from environment_identity import current_environment_identity
    from model_identity import snapshot_digest
    from source_identity import backend_source_identity


def prepare_image(
    image: Image.Image,
    foreground_ratio: float,
    resize_foreground: Callable[[Image.Image, float], Image.Image],
) -> Image.Image:
    import numpy as np
    from PIL import Image

    source = image.convert("RGBA")
    if not np.any(np.asarray(source, dtype=np.uint8)[:, :, 3]):
        raise ValueError("TripoSR input alpha contains no foreground pixels")
    rgba = resize_foreground(source, foreground_ratio)
    if not np.any(np.asarray(rgba, dtype=np.uint8)[:, :, 3]):
        raise ValueError("TripoSR resized input alpha contains no foreground pixels")
    pixels = np.asarray(rgba).astype(np.float32) / 255.0
    rgb = pixels[:, :, :3] * pixels[:, :, 3:4] + (1 - pixels[:, :, 3:4]) * 0.5
    return Image.fromarray((rgb * 255.0).astype(np.uint8))


def configure_runtime_environment(work_dir: Path) -> None:
    os.environ.setdefault("NUMBA_CACHE_DIR", str(work_dir / "numba-cache"))


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: triposr_runner.py REQUEST.json RESPONSE.json")
    request_path = Path(sys.argv[1])
    response_path = Path(sys.argv[2])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    repo = Path(request["repo"])
    configure_runtime_environment(request_path.parent)
    expected_source = request["backend_source"]
    if backend_source_identity(repo) != expected_source:
        raise RuntimeError("TripoSR checkout changed before Backend import")
    sys.path.insert(0, str(repo))

    import torch
    from PIL import Image
    from tsr.system import TSR
    from tsr.utils import resize_foreground

    if not torch.cuda.is_available():
        raise RuntimeError("TripoSR requires an accessible CUDA GPU")
    local_model = Path(request["model"])
    if local_model.is_dir():
        snapshot = local_model
    else:
        from huggingface_hub import snapshot_download

        snapshot = Path(snapshot_download(request["model"], local_files_only=True))
    model_digest = snapshot_digest(snapshot)
    model = TSR.from_pretrained(snapshot, config_name="config.yaml", weight_name="model.ckpt")
    model.renderer.set_chunk_size(int(request["chunk_size"]))
    model.to("cuda")
    model.eval()

    with Image.open(request["input_image"]) as source:
        image = prepare_image(source, float(request["foreground_ratio"]), resize_foreground)

    torch.cuda.reset_peak_memory_stats()
    with torch.inference_mode():
        scene_codes = model([image], device="cuda")
        meshes = model.extract_mesh(
            scene_codes,
            has_vertex_color=True,
            resolution=int(request["mc_resolution"]),
        )
    if len(meshes) != 1:
        raise RuntimeError(f"TripoSR returned {len(meshes)} meshes for one input")
    mesh = meshes[0]
    if len(mesh.vertices) == 0 or len(mesh.faces) == 0:
        raise RuntimeError("TripoSR returned empty mesh geometry")
    mesh.export(request["output_glb"], file_type="glb")
    source_after = backend_source_identity(repo)
    if source_after != expected_source:
        raise RuntimeError("TripoSR checkout changed during Backend execution")

    revision = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    response_path.write_text(
        json.dumps(
            {
                "backend": "triposr",
                "backend_version": f"workspace-{revision}{'-dirty' if dirty else ''}",
                "model": request["model"],
                "model_digest": model_digest,
                "seed": request["seed"],
                "seed_effective": False,
                "pipeline_type": request["pipeline_type"],
                "pipeline_type_effective": False,
                "chunk_size": request["chunk_size"],
                "mc_resolution": request["mc_resolution"],
                "foreground_ratio": request["foreground_ratio"],
                "vertex_count": int(len(mesh.vertices)),
                "face_count": int(len(mesh.faces)),
                "peak_cuda_memory_mb": torch.cuda.max_memory_allocated() / 1024 / 1024,
                "backend_environment": current_environment_identity(),
                "backend_source": source_after,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

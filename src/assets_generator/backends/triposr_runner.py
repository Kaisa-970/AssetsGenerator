from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

if __package__:
    from .model_identity import snapshot_digest
else:
    from model_identity import snapshot_digest


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: triposr_runner.py REQUEST.json RESPONSE.json")
    request_path = Path(sys.argv[1])
    response_path = Path(sys.argv[2])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    repo = Path(request["repo"])
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

    image = Image.open(request["input_image"]).convert("RGBA")
    image = resize_foreground(image, float(request["foreground_ratio"]))

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
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

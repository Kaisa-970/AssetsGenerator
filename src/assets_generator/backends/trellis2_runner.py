from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: trellis2_runner.py REQUEST.json RESPONSE.json")
    request_path = Path(sys.argv[1])
    response_path = Path(sys.argv[2])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    repo = Path(request["repo"])
    sys.path.insert(0, str(repo))
    os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    import o_voxel
    import torch
    from PIL import Image
    from trellis2.pipelines import Trellis2ImageTo3DPipeline

    if not torch.cuda.is_available():
        raise RuntimeError("TRELLIS.2 requires an accessible CUDA GPU")
    pipeline = Trellis2ImageTo3DPipeline.from_pretrained(request["model"])
    pipeline.cuda()
    image = Image.open(request["input_image"]).convert("RGBA")
    mesh = pipeline.run(
        image,
        seed=int(request["seed"]),
        pipeline_type=request["pipeline_type"],
    )[0]
    mesh.simplify(16_777_216)
    glb = o_voxel.postprocess.to_glb(
        vertices=mesh.vertices,
        faces=mesh.faces,
        attr_volume=mesh.attrs,
        coords=mesh.coords,
        attr_layout=mesh.layout,
        voxel_size=mesh.voxel_size,
        aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
        decimation_target=int(request["decimation_target"]),
        texture_size=int(request["texture_size"]),
        remesh=True,
        remesh_band=1,
        remesh_project=0,
        verbose=True,
    )
    glb.export(request["output_glb"], extension_webp=False)
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
    model_path = Path(request["model"])
    if model_path.is_dir():
        config = model_path / "pipeline.json"
        model_digest = f"sha256:{hashlib.sha256(config.read_bytes()).hexdigest()}"
    else:
        from huggingface_hub import snapshot_download

        snapshot = Path(snapshot_download(request["model"], local_files_only=True))
        revision = snapshot.name
        snapshot_identity = hashlib.sha256()
        for path in sorted(item for item in snapshot.rglob("*") if item.is_file()):
            snapshot_identity.update(str(path.relative_to(snapshot)).encode())
            snapshot_identity.update(b"\0")
            snapshot_identity.update(path.resolve().name.encode())
            snapshot_identity.update(b"\0")
        model_digest = f"sha256:{snapshot_identity.hexdigest()}"
    response_path.write_text(
        json.dumps(
            {
                "backend": "trellis2",
                "backend_version": f"workspace-{revision}{'-dirty' if dirty else ''}",
                "model": request["model"],
                "model_digest": model_digest,
                "seed": request["seed"],
                "pipeline_type": request["pipeline_type"],
                "vertex_count": int(mesh.vertices.shape[0]),
                "face_count": int(mesh.faces.shape[0]),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def _snapshot_digest(snapshot: Path) -> str:
    identity = hashlib.sha256()
    for path in sorted(item for item in snapshot.rglob("*") if item.is_file()):
        identity.update(str(path.relative_to(snapshot)).encode())
        identity.update(b"\0")
        identity.update(path.resolve().name.encode())
        identity.update(b"\0")
    return f"sha256:{identity.hexdigest()}"


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: birefnet_runner.py REQUEST.json RESPONSE.json")
    request_path = Path(sys.argv[1])
    response_path = Path(sys.argv[2])
    request = json.loads(request_path.read_text(encoding="utf-8"))

    import numpy as np
    import torch
    from huggingface_hub import snapshot_download
    from PIL import Image
    from torchvision import transforms
    from transformers import AutoModelForImageSegmentation

    if not torch.cuda.is_available():
        raise RuntimeError("BiRefNet segmentation requires an accessible CUDA GPU")
    snapshot = Path(
        snapshot_download(
            request["model"],
            revision=request["revision"],
            local_files_only=True,
        )
    )
    model = (
        AutoModelForImageSegmentation.from_pretrained(
            snapshot,
            trust_remote_code=True,
            local_files_only=True,
        )
        .eval()
        .cuda()
    )
    image = Image.open(request["input_image"]).convert("RGB")
    transform = transforms.Compose(
        [
            transforms.Resize((1024, 1024)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ]
    )
    torch.cuda.reset_peak_memory_stats()
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16):
        prediction = model(transform(image).unsqueeze(0).cuda())[-1].sigmoid().float().cpu()[0, 0]
    soft_mask = transforms.ToPILImage()(prediction).resize(image.size, Image.Resampling.BILINEAR)
    values = np.asarray(soft_mask)
    binary = np.where(values >= int(request["threshold"]), 255, 0).astype(np.uint8)
    Image.fromarray(binary, mode="L").save(request["output_mask"], format="PNG")
    response_path.write_text(
        json.dumps(
            {
                "backend": "birefnet_lite",
                "backend_version": "transformers-remote-code",
                "model": request["model"],
                "model_revision": request["revision"],
                "model_digest": _snapshot_digest(snapshot),
                "threshold": request["threshold"],
                "foreground_ratio": float((binary > 0).mean()),
                "peak_cuda_memory_mb": torch.cuda.max_memory_allocated() / 1024 / 1024,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

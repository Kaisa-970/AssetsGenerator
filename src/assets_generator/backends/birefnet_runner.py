from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__:
    from .model_identity import snapshot_digest as _snapshot_digest
    from .model_identity import snapshot_state
else:
    from model_identity import snapshot_digest as _snapshot_digest
    from model_identity import snapshot_state


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: birefnet_runner.py REQUEST.json RESPONSE.json")
    request_path = Path(sys.argv[1])
    response_path = Path(sys.argv[2])
    request = json.loads(request_path.read_text(encoding="utf-8"))

    local = Path(request["model"])
    if local.is_dir():
        snapshot = local
    else:
        from huggingface_hub import snapshot_download

        snapshot = Path(
            snapshot_download(request["model"], revision=request["revision"], local_files_only=True)
        )
    before = snapshot_state(snapshot)
    digest = _snapshot_digest(snapshot)
    if before != snapshot_state(snapshot):
        raise RuntimeError("model changed while computing identity")
    if request.get("action") == "identity":
        response_path.write_text(
            json.dumps(
                {
                    "model_digest": digest,
                    "snapshot_path": str(snapshot.absolute()),
                    "snapshot_state": before,
                }
            ),
            encoding="utf-8",
        )
        return 0
    if request.get("expected_model_digest") != digest:
        raise RuntimeError("model snapshot changed after cache lookup")

    import numpy as np
    import torch
    from PIL import Image
    from torchvision import transforms
    from transformers import AutoModelForImageSegmentation

    if not torch.cuda.is_available():
        raise RuntimeError("BiRefNet segmentation requires an accessible CUDA GPU")
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
                "model_digest": digest,
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

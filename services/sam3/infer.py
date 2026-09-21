"""Executed only inside the existing independent SAM3 environment."""

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from sam3.model.sam3_image_processor import Sam3Processor
from sam3.model_builder import build_sam3_image_model

p = argparse.ArgumentParser()
p.add_argument("--checkpoint", required=True)
p.add_argument("--image", required=True)
p.add_argument("--parameters", required=True)
p.add_argument("--output", required=True)
a = p.parse_args()
options = json.loads(Path(a.parameters).read_text())
output = Path(a.output)
output.mkdir(exist_ok=False)
model = build_sam3_image_model(
    checkpoint_path=a.checkpoint, load_from_HF=False, device="cuda", compile=False
)
processor = Sam3Processor(model, confidence_threshold=options["confidence"], device="cuda")
with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
    state = processor.set_image(Image.open(a.image).convert("RGB"))
    result = processor.set_text_prompt(prompt=options["prompt"], state=state)
masks = result["masks"].detach().float().cpu().numpy()
scores = result["scores"].detach().float().cpu().numpy().reshape(-1)
boxes = result["boxes"].detach().float().cpu().numpy()
if len(masks) > 64:
    raise ValueError("more than 64 candidates; narrow the prompt")
items = []
for mask, score, box in zip(masks, scores, boxes, strict=True):
    mask = np.squeeze(mask)
    if mask.ndim != 2:
        raise ValueError("invalid SAM3 mask dimensions")
    binary = (mask > 0.5).astype(np.uint8) * 255
    if not binary.any():
        continue
    name = f"mask_{len(items)}"
    Image.fromarray(binary).save(output / (name + ".png"))
    items.append({"output_id": name, "score": float(score), "box": box.tolist()})
(output / "candidates.json").write_text(json.dumps(items, allow_nan=False))

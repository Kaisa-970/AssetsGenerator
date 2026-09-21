"""Shared text segmentation boundary, without model imports."""

import io
import math
from typing import Any

from PIL import Image


def parameters(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"prompt", "confidence"}:
        raise ValueError("text segmentation requires prompt and confidence")
    prompt, confidence = raw["prompt"], raw["confidence"]
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 256:
        raise ValueError("prompt must be nonempty and at most 256 characters")
    if (
        type(confidence) not in (int, float)
        or not math.isfinite(confidence)
        or not 0 < confidence < 1
    ):
        raise ValueError("confidence must be finite and in (0, 1)")
    return {"prompt": prompt, "confidence": float(confidence)}


def image_size(data: bytes) -> tuple[int, int]:
    if not 0 < len(data) <= 20 * 1024 * 1024:
        raise ValueError("text segmentation image exceeds limit")
    with Image.open(io.BytesIO(data)) as image:
        if (
            image.mode != "RGB"
            or "transparency" in image.info
            or image.format not in {"PNG", "JPEG"}
            or image.width * image.height > 25_000_000
            or getattr(image, "n_frames", 1) != 1
            or image.getexif().get(274, 1) != 1
        ):
            raise ValueError("text segmentation requires orientation-free RGB image")
        image.load()
        return image.size


def validate_mask(data: bytes, size: tuple[int, int]) -> None:
    if not 0 < len(data) <= 20 * 1024 * 1024:
        raise ValueError("mask size exceeds limit")
    with Image.open(io.BytesIO(data)) as mask:
        if (
            mask.format != "PNG"
            or "transparency" in mask.info
            or mask.getexif().get(274, 1) != 1
            or mask.mode != "L"
            or mask.size != size
            or getattr(mask, "n_frames", 1) != 1
        ):
            raise ValueError("candidate must be a same-size grayscale PNG")
        values = set(mask.tobytes())
        if not values <= {0, 255} or 255 not in values:
            raise ValueError("candidate mask must be binary and nonempty")

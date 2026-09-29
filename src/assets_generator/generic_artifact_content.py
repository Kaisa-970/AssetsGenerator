"""Content checks shared by generic remote models, independent of model names."""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

from .contracts import ContractError


def image_metadata(data: bytes, kind: str, schema: str, version: str, media: str) -> dict[str, Any]:
    """Decode the actual raster; never relabel bytes or silently remove alpha."""
    if (schema, version, media) != ("png", "1.0", "image/png"):
        raise ContractError("generic PNG image requires png@1.0 and image/png")
    expected = {"rgb_image": "RGB", "rgba_image": "RGBA", "binary_mask": "L"}[kind]
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 64 * 1024 * 1024:
                raise ContractError("generic image exceeds pixel limit")
            if (
                image.format != "PNG"
                or image.mode != expected
                or getattr(image, "n_frames", 1) != 1
                or "transparency" in image.info
                or image.getexif().get(274, 1) != 1
            ):
                raise ContractError("generic image encoding disagrees with declared kind")
            image.load()
            if kind == "binary_mask" and any(
                v not in {0, 255} for _, v in (image.getcolors(257) or [(0, -1)])
            ):
                raise ContractError("generic binary mask contains nonbinary pixels")
            return {"media_type": media, "channel_layout": expected}
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ContractError("invalid generic image content") from error

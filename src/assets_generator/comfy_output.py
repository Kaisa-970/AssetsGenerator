"""Explicit image output selection and encoding validation for ComfyUI boundaries."""

from __future__ import annotations

import io
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlencode

from PIL import Image


def image_query(observation: dict[str, Any], node: str, index: int) -> str:
    if observation.get("state") != "succeeded":
        raise ValueError("ComfyUI image requires a successful fixed observation")
    if type(index) is not int or index < 0:
        raise ValueError("image index must be nonnegative")
    outputs = observation.get("outputs", {}).get(node, {}).get("images")
    if not isinstance(outputs, list) or index >= len(outputs):
        raise ValueError("configured ComfyUI image output missing")
    descriptor = outputs[index]
    if not isinstance(descriptor, dict) or set(descriptor) != {"filename", "subfolder", "type"}:
        raise ValueError("invalid ComfyUI image descriptor")
    if descriptor["type"] != "output":
        raise ValueError("ComfyUI image must belong to output storage")
    for key in ("filename", "subfolder"):
        value = descriptor[key]
        if not isinstance(value, str):
            raise ValueError("invalid ComfyUI image path")
        if key == "subfolder" and value == "":
            continue
        path = PurePosixPath(value)
        if (
            not value
            or value == "."
            or path.is_absolute()
            or PureWindowsPath(value).drive
            or ".." in path.parts
            or path.as_posix() != value
            or "\\" in value
            or any(ord(char) < 32 for char in value)
            or (key == "filename" and len(path.parts) != 1)
        ):
            raise ValueError("unsafe ComfyUI image path")
    return "/view?" + urlencode(descriptor)


def validate_png(data: bytes, *, mode: str) -> None:
    """Validate without converting image semantics or silently discarding alpha."""
    if mode not in {"RGB", "RGBA"}:
        raise ValueError("ComfyUI image boundary requires RGB or RGBA")
    with Image.open(io.BytesIO(data)) as image:
        if image.format != "PNG" or image.mode != mode or image.width * image.height > 25_000_000:
            raise ValueError("ComfyUI image encoding differs from declared contract")
        image.load()
        if mode == "RGBA" and image.getchannel("A").getextrema()[1] == 0:
            raise ValueError("ComfyUI RGBA image has no foreground")

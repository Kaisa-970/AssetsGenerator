import io
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image

from assets_generator.comfy_output import image_query, validate_png


def observation(filename="result.png", subfolder="", kind="output"):
    return {
        "state": "succeeded",
        "outputs": {
            "7": {"images": [{"filename": filename, "subfolder": subfolder, "type": kind}]}
        },
    }


def test_output_mapping_is_explicit_and_query_encoded():
    query = image_query(observation("a & b.png", "job/one"), "7", 0)
    assert parse_qs(urlsplit(query).query) == {
        "filename": ["a & b.png"],
        "subfolder": ["job/one"],
        "type": ["output"],
    }
    for node, index in (("missing", 0), ("7", -1), ("7", True), ("7", 1)):
        with pytest.raises(ValueError):
            image_query(observation(), node, index)


@pytest.mark.parametrize(
    "field,value",
    [
        ("filename", "../x"),
        ("filename", "x/y"),
        ("filename", "C:x"),
        ("filename", "x\x00.png"),
        ("subfolder", "/tmp"),
        ("subfolder", "a/../b"),
        ("subfolder", "a//b"),
        ("subfolder", "a\\b"),
        ("type", "input"),
        ("type", "temp"),
    ],
)
def test_unsafe_or_unintended_output_rejected(field, value):
    raw = observation()
    raw["outputs"]["7"]["images"][0][field] = value
    with pytest.raises(ValueError):
        image_query(raw, "7", 0)


def test_png_contract_no_implicit_conversion():
    data = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(data, format="PNG")
    validate_png(data.getvalue(), mode="RGB")
    with pytest.raises(ValueError):
        validate_png(data.getvalue(), mode="RGBA")
    empty = io.BytesIO()
    Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(empty, format="PNG")
    with pytest.raises(ValueError, match="foreground"):
        validate_png(empty.getvalue(), mode="RGBA")
    with pytest.raises(OSError):
        validate_png(b"not a texture", mode="RGB")

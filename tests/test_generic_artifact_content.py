import io

import pytest
from PIL import Image

from assets_generator.contracts import ContractError
from assets_generator.generic_artifact_content import image_metadata


def png(mode="RGB", color=0, **options):
    out = io.BytesIO()
    Image.new(mode, (3, 2), color).save(out, format="PNG", **options)
    return out.getvalue()


def test_rgb_content_supplies_downstream_metadata():
    assert image_metadata(png(), "rgb_image", "png", "1.0", "image/png") == {
        "media_type": "image/png",
        "channel_layout": "RGB",
    }


@pytest.mark.parametrize("data", [b"not a PNG", png("RGBA"), png(transparency=(0, 0, 0))])
def test_rgb_rejects_invalid_content_and_alpha(data):
    with pytest.raises(ContractError):
        image_metadata(data, "rgb_image", "png", "1.0", "image/png")


def test_mask_must_contain_binary_pixels():
    assert (
        image_metadata(png("L", 255), "binary_mask", "png", "1.0", "image/png")["channel_layout"]
        == "L"
    )
    with pytest.raises(ContractError, match="nonbinary"):
        image_metadata(png("L", 128), "binary_mask", "png", "1.0", "image/png")


def test_image_rejects_unimplemented_schema_version():
    with pytest.raises(ContractError):
        image_metadata(png(), "rgb_image", "png", "9.0", "image/png")

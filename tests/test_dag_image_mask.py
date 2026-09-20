import io

import pytest
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.dag_adapters import NodeExecutionContext
from assets_generator.dag_image_mask import ApplyBinaryMaskAdapter


def _png(image: Image.Image) -> bytes:
    data = io.BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


def test_apply_binary_mask_creates_rgba_with_exact_alpha(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    image = store.persist_bytes(
        _png(Image.new("RGB", (2, 2), (20, 40, 60))),
        kind="rgb_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGB"},
    )
    mask = store.persist_bytes(
        _png(Image.fromarray(__import__("numpy").array([[255, 0], [0, 255]], dtype="uint8"))),
        kind="binary_mask",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "L"},
    )
    result = (
        ApplyBinaryMaskAdapter()
        .execute(NodeExecutionContext("run", "mask", {"image": image, "mask": mask}, {}, store))
        .outputs["rgba"]
    )
    with Image.open(store.blob_path(result)) as output:
        assert output.mode == "RGBA"
        assert list(output.getchannel("A").tobytes()) == [255, 0, 0, 255]


@pytest.mark.parametrize("mask_values", [[[0, 0], [0, 0]], [[128, 0], [0, 255]]])
def test_apply_binary_mask_rejects_empty_or_non_binary_masks(tmp_path, mask_values):
    store = LocalArtifactStore(tmp_path / "store")
    image = store.persist_bytes(
        _png(Image.new("RGB", (2, 2), "red")),
        kind="rgb_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGB"},
    )
    mask = store.persist_bytes(
        _png(Image.fromarray(__import__("numpy").array(mask_values, dtype="uint8"))),
        kind="binary_mask",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "L"},
    )
    with pytest.raises(ContractError, match="0/255|foreground"):
        ApplyBinaryMaskAdapter().execute(
            NodeExecutionContext("run", "mask", {"image": image, "mask": mask}, {}, store)
        )

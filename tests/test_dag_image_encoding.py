import pytest
from PIL import Image

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.contracts import ContractError
from assets_generator.dag_adapters import NodeExecutionContext
from assets_generator.dag_image_encoding import EncodePngAdapter
from assets_generator.workflow import _import_image


@pytest.mark.parametrize("encoding", ["PNG", "JPEG"])
def test_explicit_encoding_preserves_rgb_pixels_and_original(tmp_path, encoding):
    store = LocalArtifactStore(tmp_path / "store")
    path = tmp_path / "input"
    Image.new("RGB", (3, 4), "red").save(path, format=encoding)
    source = _import_image(store, path, "rgb_image")
    context = NodeExecutionContext("run", "encode", {"image": source}, {}, store)
    result = EncodePngAdapter().execute(context).outputs["image"]
    assert result != source
    assert store.blob_path(source).read_bytes() == path.read_bytes()
    assert store.get_manifest(result.artifact_id).identity.schema_name == "png"
    with Image.open(path) as before, Image.open(store.blob_path(result)) as after:
        assert before.tobytes() == after.tobytes()
        assert after.format == "PNG"


@pytest.mark.parametrize("mode", ["RGBA", "L", "P"])
def test_encoding_rejects_implicit_channel_conversion(tmp_path, mode):
    store = LocalArtifactStore(tmp_path / "store")
    path = tmp_path / "input.png"
    Image.new(mode, (3, 4)).save(path)
    source = _import_image(store, path, "rgb_image")
    with pytest.raises(ContractError, match="single opaque RGB"):
        EncodePngAdapter().execute(
            NodeExecutionContext("run", "encode", {"image": source}, {}, store)
        )


def test_encoding_rejects_rgb_transparent_color_key(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    path = tmp_path / "transparent.png"
    Image.new("RGB", (2, 2), "red").save(path, transparency=(255, 0, 0))
    source = _import_image(store, path, "rgb_image")
    with Image.open(path) as image:
        assert image.mode == "RGB"
        assert image.convert("RGBA").getpixel((0, 0))[3] == 0
    with pytest.raises(ContractError, match="opaque RGB"):
        EncodePngAdapter().execute(
            NodeExecutionContext("run", "encode", {"image": source}, {}, store)
        )

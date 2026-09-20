import io

import pytest
from PIL import Image
from test_remote_http import request

from assets_generator.artifact_store import LocalArtifactStore
from assets_generator.remote_protocol import RemoteRequest
from assets_generator.remote_service_store import RemoteServiceStore
from assets_generator.remote_shape_input import import_shape_rgba
from assets_generator.serialization import to_primitive


@pytest.mark.parametrize(
    "mode,alpha,valid", [("RGBA", 255, True), ("RGBA", 0, False), ("RGB", 255, False)]
)
def test_shape_input_encoding_and_foreground(tmp_path, mode, alpha, valid):
    source = LocalArtifactStore(tmp_path / "source")
    destination = LocalArtifactStore(tmp_path / "destination")
    out = io.BytesIO()
    Image.new(mode, (2, 2), (255, 0, 0, alpha) if mode == "RGBA" else (255, 0, 0)).save(
        out, format="PNG"
    )
    ref = source.persist_bytes(
        out.getvalue(),
        kind="rgba_image",
        schema_name="png",
        schema_version="1.0",
        identity_metadata={"media_type": "image/png", "channel_layout": "RGBA"},
    )
    identity = source.get_manifest(ref.artifact_id).identity
    req = RemoteRequest.create(
        request().identity,
        "shape",
        {
            "input_blobs": {
                "rgba": {"artifact_id": ref.artifact_id, "identity": to_primitive(identity)}
            }
        },
    )
    service = RemoteServiceStore(tmp_path / "service.sqlite", req.identity)
    try:
        service.put_blob(out.getvalue(), identity.blob_digest)
        if valid:
            assert import_shape_rgba(req, service, destination) == ref
            assert destination.blob_path(ref).read_bytes() == out.getvalue()
        else:
            with pytest.raises(ValueError):
                import_shape_rgba(req, service, destination)
    finally:
        service.close()

"""Explicit RGB raster-to-PNG encoding; never silently discard alpha or change kind."""

import io

from PIL import Image

from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .models import ArtifactRef


class EncodePngAdapter:
    spec = AdapterSpec("encode_png", "1", ("encode_png@1",))

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        source = context.inputs["image"]
        if not isinstance(source, ArtifactRef) or not context.store.verify_digest(source):
            raise ContractError("PNG encoding requires intact image evidence")
        identity = context.store.get_manifest(source.artifact_id).identity
        if (identity.kind, identity.schema_name, identity.schema_version) != (
            "rgb_image",
            "raster_image",
            "1.0",
        ):
            raise ContractError("PNG encoding requires RGB raster_image@1.0")
        with Image.open(context.store.blob_path(source)) as image:
            image.load()
            if image.mode != "RGB" or getattr(image, "n_frames", 1) != 1:
                raise ContractError("PNG encoding requires a single RGB frame")
            metadata = identity.identity_metadata
            if (
                metadata.get("channel_layout") != image.mode
                or metadata.get("width") != image.width
                or metadata.get("height") != image.height
                or metadata.get("media_type") != Image.MIME.get(image.format or "")
            ):
                raise ContractError("PNG encoding source metadata mismatch")
            output = io.BytesIO()
            image.save(output, format="PNG")
        ref = context.store.persist_bytes(
            output.getvalue(),
            kind="rgb_image",
            schema_name="png",
            schema_version="1.0",
            identity_metadata={"media_type": "image/png", "channel_layout": "RGB"},
        )
        return NodeExecutionResult({"image": ref})

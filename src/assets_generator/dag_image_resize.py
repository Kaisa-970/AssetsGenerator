"""Explicit pixel-space RGB resizing, without camera or mask transformation."""

import io

from PIL import Image

from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .models import ArtifactRef


class ResizeImageAdapter:
    spec = AdapterSpec(
        "resize_image",
        "1",
        ("resize_image@1",),
        parameter_schema={
            "type": "object",
            "properties": {
                "width": {"type": "integer", "minimum": 1, "maximum": 4096},
                "height": {"type": "integer", "minimum": 1, "maximum": 4096},
                "resampling": {"type": "string", "enum": ["nearest", "bilinear", "lanczos"]},
            },
            "required": ["width", "height", "resampling"],
            "additionalProperties": False,
        },
        defaults={"width": 512, "height": 512, "resampling": "lanczos"},
    )

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        source = context.inputs["image"]
        if not isinstance(source, ArtifactRef) or not context.store.verify_digest(source):
            raise ContractError("resize requires intact RGB PNG evidence")
        identity = context.store.get_manifest(source.artifact_id).identity
        if (identity.kind, identity.schema_name, identity.schema_version) != (
            "rgb_image",
            "png",
            "1.0",
        ):
            raise ContractError("resize requires rgb_image png@1.0")
        width, height = context.parameters["width"], context.parameters["height"]
        filters = {
            "nearest": Image.Resampling.NEAREST,
            "bilinear": Image.Resampling.BILINEAR,
            "lanczos": Image.Resampling.LANCZOS,
        }
        method = context.parameters["resampling"]
        if (
            type(width) is not int
            or not 1 <= width <= 4096
            or type(height) is not int
            or not 1 <= height <= 4096
            or not isinstance(method, str)
            or method not in filters
        ):
            raise ContractError("invalid resize dimensions or resampling")
        with Image.open(context.store.blob_path(source)) as image:
            image.load()
            if (
                image.format != "PNG"
                or image.mode != "RGB"
                or "transparency" in image.info
                or getattr(image, "n_frames", 1) != 1
                or identity.identity_metadata.get("channel_layout") != "RGB"
                or identity.identity_metadata.get("media_type") != "image/png"
            ):
                raise ContractError("resize requires a single RGB PNG with matching metadata")
            resized = image.resize((width, height), filters[method])
            # Do not carry stale EXIF/camera/dimension metadata into the new pixels.
            resized.info.clear()
            output = io.BytesIO()
            resized.save(output, format="PNG")
        ref = context.store.persist_bytes(
            output.getvalue(),
            kind="rgb_image",
            schema_name="png",
            schema_version="1.0",
            identity_metadata={"media_type": "image/png", "channel_layout": "RGB"},
        )
        return NodeExecutionResult({"image": ref})

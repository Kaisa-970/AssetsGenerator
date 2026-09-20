"""Deterministic RGB image and binary mask compositing for editor pipelines."""

import io

from PIL import Image

from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .models import ArtifactRef


class ApplyBinaryMaskAdapter:
    spec = AdapterSpec("apply_binary_mask", "1", ("apply_binary_mask@1",))

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        image = context.inputs.get("image")
        mask = context.inputs.get("mask")
        if not isinstance(image, ArtifactRef) or not isinstance(mask, ArtifactRef):
            raise ContractError("mask compositing requires image and mask ArtifactRefs")
        if not context.store.verify_digest(image) or not context.store.verify_digest(mask):
            raise ContractError("mask compositing requires intact image and mask evidence")
        image_identity = context.store.get_manifest(image.artifact_id).identity
        mask_identity = context.store.get_manifest(mask.artifact_id).identity
        if image_identity.kind != "rgb_image" or image_identity.schema_version != "1.0":
            raise ContractError("mask compositing requires an RGB image")
        if (mask_identity.kind, mask_identity.schema_name, mask_identity.schema_version) != (
            "binary_mask",
            "png",
            "1.0",
        ):
            raise ContractError("mask compositing requires a binary PNG mask")
        with (
            Image.open(context.store.blob_path(image)) as source,
            Image.open(context.store.blob_path(mask)) as mask_image,
        ):
            source.load()
            mask_image.load()
            if (
                source.mode != "RGB"
                or getattr(source, "n_frames", 1) != 1
                or "transparency" in source.info
            ):
                raise ContractError("mask compositing requires a single opaque RGB frame")
            if (
                mask_image.format != "PNG"
                or mask_image.mode not in {"1", "L"}
                or "transparency" in mask_image.info
            ):
                raise ContractError(
                    "mask compositing requires a grayscale PNG without transparency"
                )
            if mask_image.size != source.size or getattr(mask_image, "n_frames", 1) != 1:
                raise ContractError("image and mask dimensions must match")
            alpha = mask_image.convert("L")
            values = set(alpha.tobytes())
            if not values <= {0, 255} or 255 not in values:
                raise ContractError("mask must contain only 0/255 values and foreground")
            rgba = source.copy()
            rgba.putalpha(alpha)
            rgba.info.clear()
            output = io.BytesIO()
            rgba.save(output, format="PNG")
        ref = context.store.persist_bytes(
            output.getvalue(),
            kind="rgba_image",
            schema_name="png",
            schema_version="1.0",
            identity_metadata={"media_type": "image/png", "channel_layout": "RGBA"},
        )
        return NodeExecutionResult({"rgba": ref})

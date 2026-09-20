"""Trusted API workflow parameter mapping; no network or executable node imports."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .compiled_plan import digest, freeze, thaw
from .dag_adapters import AdapterSpec


def validate_prompt(prompt: dict[str, Any]) -> None:
    if not isinstance(prompt, dict) or not prompt:
        raise ValueError("ComfyUI requires a nonempty API workflow")
    dependencies: dict[str, set[str]] = {}
    for node_id, node in prompt.items():
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("ComfyUI node IDs must be nonempty strings")
        if (
            not isinstance(node, dict)
            or set(node) - {"class_type", "inputs", "_meta"}
            or not isinstance(node.get("class_type"), str)
            or not node["class_type"]
            or not isinstance(node.get("inputs"), dict)
        ):
            raise ValueError("invalid ComfyUI API node")
        dependencies[node_id] = set()
        for name, value in node["inputs"].items():
            if not isinstance(name, str) or not name:
                raise ValueError("invalid ComfyUI input name")
            if isinstance(value, list):
                if (
                    len(value) != 2
                    or not isinstance(value[0], str)
                    or value[0] not in prompt
                    or type(value[1]) is not int
                    or value[1] < 0
                ):
                    raise ValueError("invalid ComfyUI input link; literal arrays unsupported")
                dependencies[node_id].add(value[0])
    # Iterative topological check avoids recursion limits on externally supplied graphs.
    done: set[str] = set()
    while len(done) < len(dependencies):
        ready = {
            node for node, parents in dependencies.items() if node not in done and parents <= done
        }
        if not ready:
            raise ValueError("ComfyUI workflow contains a cycle")
        done.update(ready)
    freeze(prompt)  # Require finite, string-keyed JSON throughout.


class ComfyWorkflow:
    def __init__(
        self,
        prompt: dict[str, Any],
        spec: AdapterSpec,
        targets: Mapping[str, tuple[str, str]],
        *,
        image_targets: Mapping[str, tuple[str, str]] | None = None,
    ):
        validate_prompt(prompt)
        properties = spec.parameter_schema.get("properties", {})
        if set(targets) != set(properties):
            raise ValueError("each exposed parameter requires exactly one workflow target")
        seen = set()
        for parameter, target in targets.items():
            if (
                not isinstance(target, tuple)
                or len(target) != 2
                or any(not isinstance(v, str) for v in target)
            ):
                raise ValueError("workflow target requires node and input")
            node, port = target
            if node not in prompt or port not in prompt[node]["inputs"] or target in seen:
                raise ValueError("missing or duplicate workflow target")
            if isinstance(prompt[node]["inputs"][port], list):
                raise ValueError("parameters cannot replace workflow links")
            if properties[parameter].get("type") not in {"string", "number", "integer", "boolean"}:
                raise ValueError("workflow parameters must be scalar")
            seen.add(target)
        image_targets = image_targets or {}
        if set(image_targets) & set(targets):
            raise ValueError("image inputs and scalar parameters must have distinct names")
        for name, target in image_targets.items():
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(target, tuple)
                or len(target) != 2
                or any(not isinstance(v, str) for v in target)
            ):
                raise ValueError("image target requires name, node and input")
            node, port = target
            if (
                node not in prompt
                or port not in prompt[node]["inputs"]
                or not isinstance(prompt[node]["inputs"][port], str)
                or target in seen
            ):
                raise ValueError("invalid or overlapping workflow image target")
            seen.add(target)
        self.prompt = freeze(prompt)
        self.spec = spec
        self.targets = freeze({name: list(target) for name, target in targets.items()})
        self.image_targets = freeze({name: list(target) for name, target in image_targets.items()})

    def bind(
        self,
        parameters: Mapping[str, Any],
        *,
        images: Mapping[str, Mapping[str, Any]] | None = None,
        endpoint: str | None = None,
    ) -> dict[str, Any]:
        """Image values come from verified upload receipts, never scalar paths.

        Receipt validation binds declarations; the trusted caller must obtain
        them with upload_image, which performs Artifact and byte verification.
        """
        actual = thaw(self.spec.normalize_parameters(parameters))
        prompt = thaw(self.prompt)
        for name, value in actual.items():
            node, port = self.targets[name]
            prompt[node]["inputs"][port] = value
        images = images or {}
        if set(images) != set(self.image_targets):
            raise ValueError("workflow requires exactly the declared image inputs")
        for name, receipt in images.items():
            _validate_image_receipt(receipt, endpoint)
            node, port = self.image_targets[name]
            prompt[node]["inputs"][port] = receipt["workflow_value"]
        validate_prompt(prompt)
        return {
            "prompt": prompt,
            "parameters": actual,
            "images": thaw(freeze(images)),
            "template_digest": digest(self.prompt),
            "workflow_digest": digest(prompt),
            "mapping_digest": digest(
                {
                    "version": "comfy-input-map@2",
                    "targets": self.targets,
                    "image_targets": self.image_targets,
                    "schema": self.spec.parameter_schema,
                    "defaults": self.spec.defaults,
                }
            ),
        }


def _validate_image_receipt(receipt: Mapping[str, Any], endpoint: str | None) -> None:
    if not isinstance(receipt, Mapping) or set(receipt) != {
        "artifact_id",
        "blob_digest",
        "endpoint",
        "filename",
        "subfolder",
        "type",
        "workflow_value",
        "verification",
    }:
        raise ValueError("invalid ComfyUI image receipt fields")
    for key in ("artifact_id", "blob_digest"):
        value = receipt[key]
        if (
            not isinstance(value, str)
            or not value.startswith("sha256:")
            or len(value) != 71
            or any(char not in "0123456789abcdef" for char in value[7:])
        ):
            raise ValueError("invalid ComfyUI image receipt digest")
    filename = "asset-" + receipt["blob_digest"][7:] + ".png"
    if (
        not endpoint
        or receipt["endpoint"] != endpoint
        or receipt["filename"] != filename
        or receipt["subfolder"] != "assets-generator"
        or receipt["type"] != "input"
        or receipt["workflow_value"] != "assets-generator/" + filename
        or receipt["verification"] != "exact-byte-readback@1"
    ):
        raise ValueError("ComfyUI image receipt location or verification mismatch")

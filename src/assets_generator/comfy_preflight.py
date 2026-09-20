"""Read-only deployment declaration checks, never inference or model attestation."""

from urllib.parse import quote

from .comfy_http import ComfyClient
from .comfy_profile import ComfyImageProfile
from .comfy_submission import ComfySubmissionUnknown
from .serialization import cache_key, to_primitive


def preflight(profile: ComfyImageProfile) -> dict[str, object]:
    raw = profile.to_dict()
    client = ComfyClient(raw["endpoint"])
    classes = sorted({node["class_type"] for node in raw["prompt"].values()})
    observations = {}
    issues = []
    output_class = raw["prompt"][raw["output"]["node"]]["class_type"]
    for name in classes:
        try:
            info = client._json("/object_info/" + quote(name, safe=""))
        except ComfySubmissionUnknown:
            issues.append(f"{name}: node metadata unavailable")
            continue
        declaration = info.get(name)
        if not isinstance(declaration, dict):
            issues.append(f"{name}: node class missing or invalid declaration")
            continue
        observations[name] = declaration
        if name == output_class and declaration.get("output_node") is not True:
            issues.append(f"{name}: selected output is not declared an output node")
    return {
        "identity": to_primitive(profile.identity),
        "endpoint": client.endpoint,
        "check": "comfy-node-declarations@1",
        "ok": not issues,
        "issues": issues,
        "node_classes": classes,
        "observed_classes": sorted(observations),
        "declarations_digest": cache_key(observations),
        "deployment_verified": False,
        "inference_submitted": False,
        "unchecked": ["input semantics", "model availability", "custom node identity", "inference"],
    }

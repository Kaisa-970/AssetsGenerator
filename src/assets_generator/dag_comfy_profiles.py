"""Register explicitly trusted ComfyUI profiles for editor/DAG node instances."""

from pathlib import Path
from typing import Any

from .comfy_profile import ComfyImageProfile
from .dag_adapters import AdapterRegistry
from .dag_comfy_image import ComfyImageAdapter


def register_comfy_profiles(registry: AdapterRegistry, raw: dict[str, Any], *, base: Path) -> None:
    if not isinstance(raw, dict) or set(raw) != {"default_profile", "profiles"}:
        raise ValueError("ComfyUI config requires default_profile and profiles")
    profiles, default = raw["profiles"], raw["default_profile"]
    if (
        not isinstance(profiles, dict)
        or not profiles
        or not isinstance(default, str)
        or default not in profiles
    ):
        raise ValueError("ComfyUI default_profile must name a configured backend")
    adapters = {}
    for name, entry in profiles.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("ComfyUI backend name required")
        if not isinstance(entry, dict) or set(entry) != {"endpoint", "profile"}:
            raise ValueError("ComfyUI backend requires gateway endpoint and profile file")
        if any(not isinstance(value, str) or not value for value in entry.values()):
            raise ValueError("ComfyUI backend fields must be nonempty strings")
        path = Path(entry["profile"]).expanduser()
        if not path.is_absolute():
            path = base / path
        adapters[name] = ComfyImageAdapter(entry["endpoint"], ComfyImageProfile.load(path))
    registry.register(adapters[default])
    for name, adapter in adapters.items():
        registry.register_backend(name, adapter)

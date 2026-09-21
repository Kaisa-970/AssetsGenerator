"""Operator-selected remote shape services from trusted startup configuration."""

from typing import Any

from .dag_adapters import AdapterRegistry
from .dag_asset_assembly import MaskedShapeAssetAssemblyAdapter, ShapeAssetAssemblyAdapter
from .dag_asset_export import AssetExportAdapter
from .dag_canonicalize import CanonicalizeAdapter
from .dag_geometry_validation import GeometryValidationAdapter
from .dag_remote_adapter import RemoteNodeAdapter
from .dag_remote_masked_shape import RemoteMaskedShapeAdapter
from .dag_remote_shape import RemoteShapeAdapter
from .dag_selection_prepare import SelectionPrepareAdapter
from .remote_protocol import RemoteIdentity


def register_remote_shape_profiles(registry: AdapterRegistry, raw: dict[str, Any]) -> None:
    if not isinstance(raw, dict) or set(raw) != {"default_profile", "profiles"}:
        raise ValueError("remote config requires default_profile and profiles")
    profiles = raw["profiles"]
    default = raw["default_profile"]
    if (
        not isinstance(profiles, dict)
        or not profiles
        or not isinstance(default, str)
        or default not in profiles
    ):
        raise ValueError("remote default_profile must name a configured service")
    adapters: dict[str, RemoteNodeAdapter] = {}
    for name, configured in profiles.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("remote profile requires a nonempty name")
        if not isinstance(configured, dict):
            raise ValueError("remote profile must be an object")
        masked = configured.get("operator") == "masked_shape_generation@1"
        fields = {"endpoint", "service_id", "backend_digest"}
        if masked:
            fields |= {"operator", "upstream_digest"}
        if set(configured) != fields:
            raise ValueError("remote profile fields differ from selected operator")
        if not all(isinstance(value, str) for value in configured.values()):
            raise ValueError("remote profile fields must be strings")
        identity = RemoteIdentity(configured["service_id"], configured["backend_digest"])
        adapters[name] = (
            RemoteMaskedShapeAdapter(
                configured["endpoint"], identity, configured["upstream_digest"]
            )
            if masked
            else RemoteShapeAdapter(configured["endpoint"], identity)
        )
    registry.register(adapters[default])
    for name, adapter in adapters.items():
        registry.register_backend(name, adapter)
    for core in (
        SelectionPrepareAdapter(),
        CanonicalizeAdapter(),
        GeometryValidationAdapter(),
        ShapeAssetAssemblyAdapter(),
        AssetExportAdapter(),
    ):
        registry.register(core)

    if any(isinstance(adapter, RemoteMaskedShapeAdapter) for adapter in adapters.values()):
        registry.register(MaskedShapeAssetAssemblyAdapter())

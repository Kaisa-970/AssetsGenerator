"""Operator-selected remote shape services from trusted startup configuration."""

from typing import Any

from .dag_adapters import AdapterRegistry
from .dag_asset_assembly import ShapeAssetAssemblyAdapter
from .dag_asset_export import AssetExportAdapter
from .dag_canonicalize import CanonicalizeAdapter
from .dag_geometry_validation import GeometryValidationAdapter
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
    adapters = {}
    for name, configured in profiles.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("remote profile requires a nonempty name")
        if not isinstance(configured, dict) or set(configured) != {
            "endpoint",
            "service_id",
            "backend_digest",
        }:
            raise ValueError("remote profile requires endpoint, service_id and backend_digest")
        if not all(isinstance(value, str) for value in configured.values()):
            raise ValueError("remote profile fields must be strings")
        adapters[name] = RemoteShapeAdapter(
            configured["endpoint"],
            RemoteIdentity(configured["service_id"], configured["backend_digest"]),
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

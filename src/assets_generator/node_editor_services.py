"""Explicit user-installed shape services; discovery never dispatches inference."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .contracts import ContractError
from .dag_adapters import AdapterRegistry
from .serialization import canonical_json_bytes, read_json, sha256_bytes
from .workbench_persistence import DurableIO


class ModelServices:
    def __init__(self, directory: Path):
        self.path = directory / "model-services" / "installed.json"
        self.io = DurableIO()
        self.lock = threading.RLock()
        self.poisoned = False
        self.entries: list[dict[str, Any]] = []
        if self.path.exists():
            raw = read_json(self.path)
            if (
                not isinstance(raw, dict)
                or set(raw) != {"schema_version", "services"}
                or raw["schema_version"] != "installed_model_services@1"
                or not isinstance(raw["services"], list)
            ):
                raise ContractError("invalid installed model service catalog")
            for item in raw["services"]:
                self._validate_entry(item)
                if any(e["backend"] == item["backend"] for e in self.entries):
                    raise ContractError("duplicate installed model service")
                self.entries.append(item)

    @staticmethod
    def _backend(detection: dict[str, Any]) -> str:
        return "model_" + sha256_bytes(canonical_json_bytes(detection)).split(":")[1][:24]

    @classmethod
    def _validate_entry(cls, item: Any) -> None:
        from .model_service_descriptor import validate_descriptor
        from .remote_http import RemoteJobClient

        if not isinstance(item, dict) or set(item) != {
            "endpoint",
            "descriptor",
            "descriptor_digest",
            "backend",
        }:
            raise ContractError("invalid installed service entry")
        descriptor = validate_descriptor(item["descriptor"])
        if RemoteJobClient(item["endpoint"]).endpoint != item["endpoint"]:
            raise ContractError("installed endpoint is not normalized")
        if sha256_bytes(canonical_json_bytes(descriptor)) != item["descriptor_digest"]:
            raise ContractError("installed descriptor digest mismatch")
        detection = {k: item[k] for k in ("endpoint", "descriptor", "descriptor_digest")}
        if cls._backend(detection) != item["backend"]:
            raise ContractError("installed Backend identity mismatch")

    def summaries(self) -> list[dict[str, Any]]:
        with self.lock:
            return [
                {
                    "backend": e["backend"],
                    "display_name": e["descriptor"]["display_name"],
                    "operator": e["descriptor"]["operator"],
                    "endpoint": e["endpoint"],
                    "descriptor_digest": e["descriptor_digest"],
                    "frame_id": e["descriptor"].get("frame_id", "unknown"),
                    "up_axis": e["descriptor"].get("up_axis", "unknown"),
                    "unit": e["descriptor"].get("unit", "unknown"),
                }
                for e in self.entries
            ]

    @staticmethod
    def register(registry: AdapterRegistry, entry: dict[str, Any]) -> None:
        from .dag_asset_assembly import ShapeAssetAssemblyAdapter
        from .dag_asset_export import AssetExportAdapter
        from .dag_canonicalize import CanonicalizeAdapter
        from .dag_geometry_validation import GeometryValidationAdapter
        from .model_service_descriptor import DiscoveredShapeAdapter

        registry.register_backend(
            entry["backend"], DiscoveredShapeAdapter(entry["endpoint"], entry["descriptor"])
        )
        existing = {item["name"] + "@" + item["version"] for item in registry.catalog()}
        for adapter in (
            CanonicalizeAdapter(),
            GeometryValidationAdapter(),
            ShapeAssetAssemblyAdapter(),
            AssetExportAdapter(),
        ):
            if adapter.spec.key not in existing:
                registry.register(adapter)

    def restore(self, registry: AdapterRegistry) -> None:
        for entry in self.entries:
            self.register(registry, entry)

    def detect(self, endpoint: str) -> dict[str, Any]:
        from .model_service_descriptor import detect_service

        return detect_service(endpoint)

    def add(self, endpoint: str, expected_digest: str, execution: Any) -> dict[str, Any]:
        detection = self.detect(endpoint)
        if detection["descriptor_digest"] != expected_digest:
            raise ContractError("服务声明已变化，请重新检测后确认添加")
        entry = {**detection, "backend": self._backend(detection)}
        self._validate_entry(entry)
        with self.lock:
            if self.poisoned:
                raise ContractError("服务目录保存状态不确定，请重启编辑器核实")
            if any(e["backend"] == entry["backend"] for e in self.entries):
                return entry

            def update(registry: AdapterRegistry) -> None:
                self.register(registry, entry)
                try:
                    self.io.write(
                        self.path,
                        canonical_json_bytes(
                            {
                                "schema_version": "installed_model_services@1",
                                "services": [*self.entries, entry],
                            }
                        ),
                    )
                except BaseException:
                    self.poisoned = True
                    raise
                self.entries.append(entry)

            execution.install_model_registry(update)
        return entry

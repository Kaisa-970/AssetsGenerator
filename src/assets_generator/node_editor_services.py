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
    def _backend(detection: dict[str, Any], capability_id: str | None = None) -> str:
        """Derive identity for one service capability, not the whole catalog."""
        from .model_service_descriptor import capability_digest, select_capability

        descriptor = detection["descriptor"]
        if capability_id is None and not descriptor.get("capabilities"):
            # Preserve v1 flat-service Backend identities.
            return "model_" + sha256_bytes(canonical_json_bytes(detection)).split(":")[1][:24]
        selected = select_capability(descriptor, capability_id)
        identity = {
            "endpoint": detection["endpoint"],
            "service_id": descriptor["service_id"],
            "backend_digest": descriptor["backend_digest"],
            "capability_id": selected["capability_id"],
            "capability_digest": capability_digest(descriptor, selected["capability_id"]),
        }
        return "model_" + sha256_bytes(canonical_json_bytes(identity)).split(":")[1][:24]

    @classmethod
    def _validate_entry(cls, item: Any) -> None:
        from .model_service_descriptor import select_capability, validate_descriptor
        from .remote_http import RemoteJobClient

        if not isinstance(item, dict):
            raise ContractError("invalid installed service entry")
        required = {"endpoint", "descriptor", "descriptor_digest", "backend"}
        if set(item) - (required | {"capability_id"}) or not required.issubset(item):
            raise ContractError("invalid installed service entry")
        descriptor = validate_descriptor(item["descriptor"])
        if RemoteJobClient(item["endpoint"]).endpoint != item["endpoint"]:
            raise ContractError("installed endpoint is not normalized")
        if sha256_bytes(canonical_json_bytes(descriptor)) != item["descriptor_digest"]:
            raise ContractError("installed descriptor digest mismatch")
        capability_id = item.get("capability_id")
        if descriptor.get("capabilities"):
            if capability_id is None:
                raise ContractError("installed multi-capability service requires capability_id")
            select_capability(descriptor, capability_id)
        else:
            capability_id = capability_id or descriptor["operator"]
        detection = {k: item[k] for k in ("endpoint", "descriptor", "descriptor_digest")}
        if cls._backend(detection, capability_id) != item["backend"]:
            # Preserve catalogs written before capability-scoped identities.
            if "capability_id" in item or cls._backend(detection) != item["backend"]:
                raise ContractError("installed Backend identity mismatch")

    def summaries(self) -> list[dict[str, Any]]:
        from .model_service_descriptor import select_capability

        with self.lock:
            return [
                {
                    "backend": e["backend"],
                    "display_name": e["descriptor"]["display_name"],
                    "operator": selected["operator"],
                    "endpoint": e["endpoint"],
                    "descriptor_digest": e["descriptor_digest"],
                    "capability_id": e.get("capability_id", e["descriptor"].get("operator")),
                    "frame_id": selected.get("frame_id", "unknown"),
                    "up_axis": selected.get("up_axis", "unknown"),
                    "unit": selected.get("unit", "unknown"),
                    **(
                        {
                            "capabilities": [
                                next(
                                    item
                                    for item in e["descriptor"]["capabilities"]
                                    if item["capability_id"] == e.get("capability_id")
                                )
                            ]
                        }
                        if e["descriptor"].get("capabilities") and e.get("capability_id")
                        else {}
                    ),
                }
                for e in self.entries
                for selected in [select_capability(e["descriptor"], e.get("capability_id"))]
            ]

    @staticmethod
    def register(registry: AdapterRegistry, entry: dict[str, Any]) -> list[Any]:
        from .dag_asset_assembly import ShapeAssetAssemblyAdapter
        from .dag_asset_export import AssetExportAdapter
        from .dag_canonicalize import CanonicalizeAdapter
        from .dag_geometry_validation import GeometryValidationAdapter
        from .model_service_adapters import discovered_service_adapter
        from .model_service_descriptor import dynamic_operator_spec, select_capability

        adapter = discovered_service_adapter(
            entry["endpoint"], entry["descriptor"], entry.get("capability_id")
        )
        registry.register_backend(entry["backend"], adapter)
        selected = select_capability(entry["descriptor"], entry.get("capability_id"))
        specs = []
        if "inputs" in selected and "outputs" in selected:
            specs.append(dynamic_operator_spec(selected))
        existing = {item["name"] + "@" + item["version"] for item in registry.catalog()}
        for adapter in (
            CanonicalizeAdapter(),
            GeometryValidationAdapter(),
            ShapeAssetAssemblyAdapter(),
            AssetExportAdapter(),
        ):
            if adapter.spec.key not in existing:
                registry.register(adapter)
        return specs

    def restore(self, registry: AdapterRegistry) -> None:
        for entry in self.entries:
            self.register(registry, entry)

    def detect(self, endpoint: str) -> dict[str, Any]:
        from .model_service_descriptor import detect_service

        return detect_service(endpoint)

    def add(
        self, endpoint: str, expected_digest: str, execution: Any, capability_id: str | None = None
    ) -> dict[str, Any]:
        detection = self.detect(endpoint)
        if detection["descriptor_digest"] != expected_digest:
            raise ContractError("服务声明已变化，请重新检测后确认添加")
        from .model_service_descriptor import select_capability

        descriptor = detection["descriptor"]
        if descriptor.get("capabilities"):
            if capability_id is None:
                if len(descriptor["capabilities"]) != 1:
                    raise ContractError("多能力服务必须选择 capability_id")
                capability_id = descriptor["capabilities"][0]["capability_id"]
            select_capability(descriptor, capability_id)
        else:
            capability_id = capability_id or descriptor["operator"]
        entry = {
            **detection,
            "backend": self._backend(
                detection, capability_id if descriptor.get("capabilities") else None
            ),
        }
        if descriptor.get("capabilities"):
            entry["capability_id"] = capability_id
        self._validate_entry(entry)
        with self.lock:
            if self.poisoned:
                raise ContractError("服务目录保存状态不确定，请重启编辑器核实")
            if any(e["backend"] == entry["backend"] for e in self.entries):
                return entry

            def update(registry: AdapterRegistry) -> None:
                dynamic_specs = self.register(registry, entry)
                for spec in dynamic_specs:
                    execution.specs[f"{spec.name}@{spec.version}"] = spec
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

"""Shared trusted local profile registry for CLI and canvas execution."""

from .dag_adapters import AdapterRegistry
from .dag_image_adapters import DagImageBuildAdapter, DagMaskSelectionAdapter, DagProposalAdapter
from .workbench_engine import BackendProfile


def image_adapter_registry(
    profiles: dict[str, BackendProfile], default_profile: str
) -> AdapterRegistry:
    if default_profile not in profiles:
        raise ValueError(f"unknown profile: {default_profile}")
    default = profiles[default_profile]
    registry = AdapterRegistry()
    for adapter in (
        DagProposalAdapter(default),
        DagMaskSelectionAdapter(),
        DagImageBuildAdapter(default),
    ):
        registry.register(adapter)
    for name, configured in profiles.items():
        registry.register_backend(name, DagProposalAdapter(configured))
        registry.register_backend(name, DagImageBuildAdapter(configured))
    return registry

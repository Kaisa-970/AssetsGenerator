"""Shared trusted local profile registry for CLI and canvas execution."""

from .dag_adapters import AdapterRegistry
from .dag_image_adapters import DagImageBuildAdapter, DagMaskSelectionAdapter, DagProposalAdapter
from .dag_multi_view import GeometryAdapter, MultiViewProfile, ReconstructionAdapter, ReleaseAdapter
from .workbench_engine import BackendProfile
from .workbench_profiles import ProposalProfile


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


def register_multi_view_profiles(
    registry: AdapterRegistry,
    profiles: dict[str, MultiViewProfile],
    default_profile: str,
) -> None:
    """Add paired geometry/reconstruction/release implementations to a catalog.

    The common profile fingerprint remains a relation constraint: choosing
    incompatible profiles across dependent nodes cannot relabel old evidence.
    """
    if default_profile not in profiles:
        raise ValueError(f"unknown multi-view profile: {default_profile}")
    adapters = (GeometryAdapter, ReconstructionAdapter, ReleaseAdapter)
    for adapter in adapters:
        registry.register(adapter(profiles[default_profile]))
    for name, configured in profiles.items():
        for adapter in adapters:
            registry.register_backend(name, adapter(configured))


def proposal_adapter_registry(
    profiles: dict[str, "ProposalProfile"], default_profile: str
) -> AdapterRegistry:
    """Register SAM and its human review without installing a local shape adapter."""
    if default_profile not in profiles:
        raise ValueError(f"unknown proposal profile: {default_profile}")
    registry = AdapterRegistry()
    registry.register(DagProposalAdapter(profiles[default_profile]))
    registry.register(DagMaskSelectionAdapter())
    for name, configured in profiles.items():
        registry.register_backend(name, DagProposalAdapter(configured))
    return registry

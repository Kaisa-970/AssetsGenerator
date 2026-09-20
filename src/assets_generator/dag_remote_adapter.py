"""Trusted remote adapters separate request construction from output semantic import."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from .contracts import ContractError
from .dag_adapters import AdapterSpec, NodeExecutionContext, NodeExecutionResult
from .models import ArtifactRef
from .remote_protocol import RemoteJob


class RemoteNodeAdapter(ABC):
    """Endpoint/service/model identity must be pinned in declared node parameters.

    prepare_payload is local and side-effect free. Import validates actual output
    encoding and Operator semantics; transport descriptors are not ArtifactRefs.
    """

    @property
    @abstractmethod
    def spec(self) -> AdapterSpec: ...

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        raise ContractError("remote adapters require remote dispatch")

    def input_blobs(self, context: NodeExecutionContext) -> Mapping[str, ArtifactRef]:
        """Explicit input artifacts to upload, drawn from validated input evidence."""
        return {}

    @abstractmethod
    def prepare_payload(self, context: NodeExecutionContext) -> dict[str, Any]: ...

    @abstractmethod
    def import_result(
        self, context: NodeExecutionContext, job: RemoteJob, blobs: Mapping[str, bytes]
    ) -> NodeExecutionResult: ...

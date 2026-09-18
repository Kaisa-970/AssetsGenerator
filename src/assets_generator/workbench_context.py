"""Optional owned execution context used only by workbench adapters."""

from __future__ import annotations

from dataclasses import dataclass

from .models import ArtifactRef, BuildRun
from .workbench_models import ChildRegistration
from .workbench_persistence import WorkbenchRepository


@dataclass(frozen=True)
class ChildRunContext:
    repository: WorkbenchRepository
    registration: ChildRegistration

    def begin(self, run: BuildRun) -> None:
        if run.run_id != self.registration.child_run_id:
            raise ValueError("child run ID does not match reservation")
        existing = self.repository.load(run.run_id)
        if (
            existing.parent_run_id != self.registration.parent_run_id
            or existing.status != "running"
            or existing.node_attempts
        ):
            raise ValueError("child execution has already started or belongs to another parent")
        run.parent_run_id = self.registration.parent_run_id
        self.persist(run)

    def persist(self, run: BuildRun) -> ArtifactRef:
        return self.repository.commit(run, owner=self.registration)

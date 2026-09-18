"""Fixed-template local execution. Effects are queued; only drain runs adapters."""

from __future__ import annotations

import json
import queue
import threading
import uuid
from collections.abc import Callable
from copy import copy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .backend_registry import ResolvedPlan
from .completion import _checked
from .contracts import (
    ContractError,
    cardinality_compatible,
    validate_operator_inputs,
    validate_operator_outputs,
)
from .errors import classify_error
from .instance_proposals import (
    InstanceProposer,
    prepare_selection_mask,
    propose_instances,
    select_instance_proposals,
)
from .models import ArtifactRef, BuildRun, StructuredValue
from .pipeline import load_default_operator_specs
from .runtime import utc_now
from .serialization import cache_key, read_json, to_primitive
from .workbench_binding import create_selection_binding
from .workbench_context import ChildRunContext
from .workbench_models import (
    ChildRegistration,
    CommandReceipt,
    ComputationInput,
    DecisionCommand,
    HumanInputRequest,
    InputBinding,
    MaskDraft,
    PlanStage,
    StageState,
    WorkbenchPlan,
    WorkbenchState,
    decode_record,
    proposal_evidence_digest,
)
from .workbench_persistence import CreationReceipt, ProcessProbe, WorkbenchRepository, _references
from .workbench_state import (
    ChildRegistered,
    ChildSucceeded,
    DecisionPrepared,
    Effect,
    Event,
    ExecutionFailed,
    HumanRequestReady,
    MaskPreviewReady,
    PrepareStage,
    RecoveryObserved,
    RetryRequested,
)
from .workflow import build_image_asset


@dataclass(frozen=True)
class BackendProfile:
    name: str
    proposer: InstanceProposer
    shape_plan: ResolvedPlan
    proposal_identity: dict[str, Any]
    shape_identity: dict[str, Any]
    # Until gated-worker integration is installed, only explicit CPU test profiles run.
    test_only: bool = True
    identity_check: Callable[[], None] | None = None


class WorkbenchEngine:
    def __init__(
        self,
        repository: WorkbenchRepository,
        profiles: dict[str, BackendProfile],
        *,
        probe: ProcessProbe | None = None,
    ) -> None:
        self.repository = repository
        self.store = repository.store
        self.profiles = profiles
        from .workbench_process import LinuxProcessProbe

        self.probe = probe or LinuxProcessProbe()
        self._queue: queue.Queue[tuple[str, Effect]] = queue.Queue()
        self._serial = threading.Lock()
        self._commands = threading.RLock()

    def _plan(self, run: BuildRun) -> WorkbenchPlan:
        if run.workbench is None:
            raise ContractError("not a workbench run")
        _checked(self.store, run.workbench.plan_ref, "workbench_plan")
        plan = decode_record(WorkbenchPlan, self.store.read_structured(run.workbench.plan_ref))
        profile = self.profiles[plan.backend_bindings["profile"]]
        if (
            plan.backend_bindings["proposal"] != profile.proposal_identity
            or plan.backend_bindings["shape"] != profile.shape_identity
            or plan.child_plan["contract_digest"] != profile.shape_plan.contract_digest
        ):
            raise ContractError("backend identity changed; create a new plan")
        expected_child = {
            "contract_digest": profile.shape_plan.contract_digest,
            "pipeline": profile.shape_plan.pipeline_name,
            "backends": {
                key: {"name": value.name, "version": value.backend_version}
                for key, value in profile.shape_plan.backends.items()
            },
        }
        if plan.child_plan != expected_child:
            raise ContractError("resolved child backend binding changed")
        if plan.contract_digests["operators"] != cache_key(load_default_operator_specs()):
            raise ContractError("operator contracts changed")
        return plan

    def _event(
        self,
        run_id: str,
        stage_id: str,
        payload: Any,
        *,
        revision: int | None = None,
        attempt: int | None = None,
    ) -> BuildRun:
        run = self.repository.load(run_id)
        assert run.workbench is not None
        stage = run.workbench.stage_states[stage_id]
        e = Event(
            f"event_{uuid.uuid4().hex}",
            run.workbench.state_revision if revision is None else revision,
            stage_id,
            attempt or stage.active_attempt or 1,
            utc_now(),
            payload,
        )
        return self.repository.apply(
            run_id, e, lambda effect: self._queue.put((run_id, effect))
        ).state

    def create(
        self,
        image: ArtifactRef,
        shape_profile: str,
        parameters: dict[str, Any],
        idempotency_key: str,
    ) -> BuildRun:
        with self._commands:
            _checked(self.store, image, "rgb_image")
            profile = self.profiles[shape_profile]
            if set(parameters) - {"seed", "pipeline_type"}:
                raise ContractError("unsupported generation parameters")
            seed = parameters.get("seed", 42)
            pipeline_type = parameters.get("pipeline_type", "512")
            if (
                type(seed) is not int
                or not 0 <= seed < 2**32
                or pipeline_type not in {"512", "1024"}
            ):
                raise ContractError("invalid generation parameters")
            parameters = {"seed": seed, "pipeline_type": pipeline_type}
            plan = WorkbenchPlan(
                {"image": image},
                [
                    PlanStage(
                        "propose",
                        "instance_proposals",
                        "1",
                        {"image": InputBinding(input_name="image")},
                        {},
                    ),
                    PlanStage(
                        "select",
                        "workbench_mask_selection",
                        "1",
                        {"proposals": InputBinding(stage_id="propose", output_port="proposals")},
                        {},
                        True,
                    ),
                    PlanStage(
                        "generate",
                        "workbench_image_workflow",
                        "1",
                        {"binding": InputBinding(stage_id="select", output_port="binding")},
                        parameters,
                    ),
                ],
                {
                    "operators": cache_key(load_default_operator_specs()),
                    "template": "photo-object-v1",
                },
                {
                    "profile": shape_profile,
                    "proposal": profile.proposal_identity,
                    "shape": profile.shape_identity,
                },
                {
                    "contract_digest": profile.shape_plan.contract_digest,
                    "pipeline": profile.shape_plan.pipeline_name,
                    "backends": {
                        key: {"name": value.name, "version": value.backend_version}
                        for key, value in profile.shape_plan.backends.items()
                    },
                },
            )
            self._compile(plan)
            request = CreationReceipt(idempotency_key, cache_key(plan), f"run_{uuid.uuid4().hex}")
            saved = self.repository.reserve_creation(request)
            index = self.store.root / "runs" / f"{saved.run_id}.json"
            if index.exists():
                return self.repository.load(saved.run_id)
            plan_ref = self.store.persist_structured(
                StructuredValue("workbench_plan", "WorkbenchPlan", "1.0", to_primitive(plan))
            )
            run = BuildRun(
                saved.run_id,
                plan.template,
                plan.template_version,
                "running",
                {"image": image},
                [],
                utc_now(),
                None,
                workbench=WorkbenchState(
                    plan_ref,
                    {s.stage_id: StageState(s.stage_id, s.human) for s in plan.stages},
                    stage_order=[s.stage_id for s in plan.stages],
                ),
            )
            self.repository.commit(run)
            self._prepare_next(run.run_id)
            return self.repository.load(run.run_id)

    def _compile(self, plan: WorkbenchPlan) -> None:
        specs = load_default_operator_specs()
        stages = {stage.stage_id: stage for stage in plan.stages}
        for stage in plan.stages:
            spec = specs[f"{stage.adapter}@{stage.adapter_version}"]
            if set(stage.inputs) != set(spec.inputs):
                raise ContractError("adapter input bindings do not match OperatorSpec")
            for name, binding in stage.inputs.items():
                target = spec.inputs[name]
                if binding.stage_id is None:
                    continue
                previous = stages[binding.stage_id]
                source = specs[f"{previous.adapter}@{previous.adapter_version}"].outputs[
                    binding.output_port or ""
                ]
                if (
                    source.kinds != target.kinds
                    or source.schema_name != target.schema_name
                    or source.schema_version != target.schema_version
                    or not cardinality_compatible(
                        source.cardinality, target.cardinality, optional=False
                    )
                ):
                    raise ContractError("incompatible adapter edge")

    def _resolved(self, run: BuildRun, stage: PlanStage, plan: WorkbenchPlan) -> ComputationInput:
        assert run.workbench is not None
        inputs: dict[str, Any] = {}
        for name, binding in stage.inputs.items():
            if binding.input_name is not None:
                inputs[name] = plan.input_refs[binding.input_name]
            else:
                assert binding.stage_id and binding.output_port
                upstream = run.workbench.stage_states[binding.stage_id]
                if upstream.status != "succeeded":
                    raise ContractError("upstream is not complete")
                inputs[name] = upstream.current().outputs[binding.output_port]
        validate_operator_inputs(
            load_default_operator_specs()[f"{stage.adapter}@{stage.adapter_version}"],
            inputs,
            self.store,
        )
        for value in inputs.values():
            if isinstance(value, ArtifactRef) and not self.store.verify_digest(value):
                raise ContractError("input artifact missing or corrupt")
        backend = (
            plan.backend_bindings["proposal" if stage.stage_id == "propose" else "shape"]
            if not stage.human
            else {"core": "mask-edit-v1"}
        )
        return ComputationInput(
            stage.adapter,
            stage.adapter_version,
            plan.contract_digests["operators"],
            inputs,
            stage.parameters,
            backend,
        )

    def _prepare_next(self, run_id: str) -> None:
        run = self.repository.load(run_id)
        plan = self._plan(run)
        assert run.workbench is not None
        for stage_plan in plan.stages:
            stage = run.workbench.stage_states[stage_plan.stage_id]
            if stage.status == "succeeded":
                continue
            if stage.status != "pending":
                return
            inputs = self._resolved(run, stage_plan, plan)
            receipt = (
                None if stage.human else self._receipt(run_id, stage.stage_id, inputs.digest())
            )
            self._event(run_id, stage.stage_id, PrepareStage(inputs, receipt), attempt=1)
            return

    def _receipt(
        self, run_id: str, stage_id: str, digest: str, key: str | None = None, kind: str = "execute"
    ) -> CommandReceipt:
        child_id = f"run_{uuid.uuid4().hex}"
        output = self.repository.directory / "executions" / run_id / child_id / "release"
        return CommandReceipt(
            key or f"command_{uuid.uuid4().hex}",
            digest,
            kind,
            child_run_id=child_id,
            output_location=str(output),
        )

    def drain(self) -> None:
        """Run queued work on one background thread, never an HTTP/commit lock."""
        with self._serial:
            while True:
                try:
                    run_id, effect = self._queue.get_nowait()
                except queue.Empty:
                    return
                try:
                    self._effect(run_id, effect)
                except Exception as error:
                    current = self.repository.load(run_id)
                    assert current.workbench is not None
                    stage = current.workbench.stage_states[effect.stage_id]
                    if stage.status != "succeeded":
                        self._event(
                            run_id,
                            effect.stage_id,
                            ExecutionFailed(classify_error(error).value, error_detail=str(error)),
                        )
                finally:
                    self._queue.task_done()

    def _effect(self, run_id: str, effect: Effect) -> None:
        run = self.repository.load(run_id)
        plan = self._plan(run)
        assert run.workbench is not None
        stage = run.workbench.stage_states[effect.stage_id]
        attempt = stage.current()
        if attempt.attempt != effect.attempt:
            return
        if effect.kind == "prepare_human_request":
            proposals = attempt.resolved_inputs["proposals"]
            assert isinstance(proposals, ArtifactRef)
            raw = self.store.read_structured(proposals)
            candidates = [{**p, "source": p.get("source", "estimated")} for p in raw["proposals"]]
            evidence = proposal_evidence_digest(
                image=plan.input_refs["image"],
                proposals=candidates,
                backend_identity=plan.backend_bindings["proposal"],
                amg_parameters={},
            )
            request = HumanInputRequest(
                run_id,
                stage.stage_id,
                attempt.attempt,
                {"proposals": proposals, "image": plan.input_refs["image"]},
                evidence,
            )
            ref = self.store.persist_structured(
                StructuredValue(
                    "human_input_request", "HumanInputRequest", "1.0", to_primitive(request)
                )
            )
            self._event(run_id, stage.stage_id, HumanRequestReady(ref, attempt.input_digest))
        elif effect.kind == "register_child":
            assert attempt.child_run_id and attempt.command_receipt
            registration = ChildRegistration(
                attempt.child_run_id,
                run_id,
                stage.stage_id,
                attempt.attempt,
                attempt.input_digest,
                str(self.store.root / "run_owners" / f"{attempt.child_run_id}.json"),
            )
            created = self.repository.register_child(run, registration)
            if not created:
                raise ContractError("child reservation exists; recover before retry")
            profile = self.profiles[plan.backend_bindings["profile"]]
            child_pipeline, child_version = (
                (profile.shape_plan.pipeline_name, profile.shape_plan.pipeline_version)
                if stage.stage_id == "generate"
                else (
                    "instance_proposals" if stage.stage_id == "propose" else "instance_selection",
                    "1",
                )
            )
            child = BuildRun(
                attempt.child_run_id,
                child_pipeline,
                child_version,
                "running",
                {},
                [],
                utc_now(),
                None,
                parent_run_id=run_id,
            )
            self.repository.commit(child, owner=registration)
            self._event(run_id, stage.stage_id, ChildRegistered(registration))
        elif effect.kind == "execute_adapter":
            if not stage.human:
                self._admit_compute(run_id, stage.stage_id, attempt.attempt)
            self._execute(run, plan, stage.stage_id)
        elif effect.kind == "release_launcher":
            # The active worker owns the channel and releases after durable authorization.
            pass
        else:
            raise ContractError(f"unsupported engine effect {effect.kind}")

    def _admit_compute(self, run_id: str, stage_id: str, attempt_number: int) -> None:
        """Serial drain cannot account for authorized orphan runners from an older service."""
        directory = str(self.repository.directory.resolve())
        for marker in (self.store.root / "parent_run_owners").glob("*.json"):
            try:
                owner = read_json(marker)
            except (OSError, ValueError) as error:
                raise ContractError("cannot verify workbench process ownership") from error
            if owner.get("workbench_directory") != directory:
                continue
            try:
                other = self.repository.load(marker.stem)
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise ContractError(f"cannot verify process evidence for {marker.stem}") from error
            if other.workbench is None:
                raise ContractError("owned run has no workbench process evidence")
            for other_stage in other.workbench.stage_states.values():
                for old in other_stage.attempts:
                    if (other.run_id, other_stage.stage_id, old.attempt) == (
                        run_id,
                        stage_id,
                        attempt_number,
                    ):
                        continue
                    worker = old.worker_execution
                    if worker is None or worker.launch_phase in {"prepared", "identity_recorded"}:
                        continue  # No durable authorization: the gate cannot launch its Backend.
                    if (
                        worker.launch_phase == "exit_observed"
                        and worker.last_probe is not None
                        and worker.last_probe.result == "exited"
                    ):
                        continue  # Durable empty-group terminal evidence survives PID reuse.
                    observation = self.probe.observe(worker.identity())
                    if observation.result != "exited":
                        raise ContractError(
                            f"compute admission blocked by {other.run_id}/{other_stage.stage_id}: "
                            f"{observation.result}; "
                            f"{observation.reason or 'process group unverified'}"
                        )

    def _verify_reference_closure(self, reference: ArtifactRef, visited: set[str]) -> None:
        if reference.artifact_id in visited:
            return
        visited.add(reference.artifact_id)
        if not self.store.verify_digest(reference):
            raise ContractError(f"missing or corrupt recovery evidence: {reference.artifact_id}")
        identity = self.store.get_manifest(reference.artifact_id).identity
        if identity.identity_metadata.get("media_type") == "application/json":
            for child in _references(
                self.store.read_structured(reference), schema_name=identity.schema_name
            ):
                self._verify_reference_closure(child, visited)

    def _execute(self, run: BuildRun, plan: WorkbenchPlan, stage_id: str) -> None:
        assert run.workbench is not None
        attempt = run.workbench.stage_states[stage_id].current()
        assert attempt.child_registration and attempt.command_receipt and attempt.child_run_id
        context = ChildRunContext(self.repository, attempt.child_registration)
        output = Path(attempt.command_receipt.output_location or "")
        private = output.parent / "inputs"
        private.mkdir(parents=True, exist_ok=True)
        image = plan.input_refs["image"]
        image_path = private / "image.png"
        image_path.write_bytes(self.store.blob_path(image).read_bytes())
        image_path.chmod(0o400)
        profile = self.profiles[plan.backend_bindings["profile"]]
        from .workbench_worker import WorkbenchProcessWorker

        gated_worker = WorkbenchProcessWorker(self, run.run_id, stage_id)
        proposer = copy(profile.proposer)
        if hasattr(proposer, "worker"):
            proposer.worker = gated_worker
        elif not profile.test_only:
            raise ContractError("real proposer does not support gated worker injection")
        shape_plan = profile.shape_plan
        shape_binding = shape_plan.backends["generate_shape"]
        implementation = copy(shape_binding.implementation)
        if hasattr(implementation, "worker"):
            implementation.worker = gated_worker
        elif not profile.test_only:
            raise ContractError("real shape backend does not support gated worker injection")
        shape_plan = replace(
            shape_plan,
            backends={
                **shape_plan.backends,
                "generate_shape": replace(shape_binding, implementation=implementation),
            },
        )
        if profile.identity_check is not None:
            profile.identity_check()
        outputs: dict[str, Any]
        if stage_id == "propose":
            result = propose_instances(
                image_path=image_path,
                store_path=self.store.root,
                output_path=output,
                backend=proposer,
                run_id=attempt.child_run_id,
                child_context=context,
            )
            outputs = {"proposals": ArtifactRef(**result["proposals"])}
            child = self.repository.load(attempt.child_run_id)
            outputs["provenance"] = child.node_attempts[0].outputs["provenance"]
        elif stage_id == "select":
            draft = run.workbench.stage_states[stage_id].draft
            assert draft is not None
            proposals = attempt.resolved_inputs["proposals"]
            assert isinstance(proposals, ArtifactRef)
            command_ref = attempt.resolved_inputs["decision_command"]
            assert isinstance(command_ref, ArtifactRef)
            command = decode_record(DecisionCommand, self.store.read_structured(command_ref))
            result = select_instance_proposals(
                proposals=proposals,
                proposal_ids=[draft.proposal_id],
                reviewer=command.reviewer,
                store_path=self.store.root,
                output_path=output,
                invert=draft.invert,
                keep_largest=draft.keep_largest,
                run_id=attempt.child_run_id,
                child_context=context,
            )
            selection = ArtifactRef(**result["selection"])
            selection_binding = create_selection_binding(
                self.store, original_proposals=proposals, selection=selection, draft=draft
            )
            outputs = {"selection": selection, "binding": selection_binding}
        else:
            binding = attempt.resolved_inputs["binding"]
            assert isinstance(binding, ArtifactRef)
            raw = self.store.read_structured(binding)
            mask = ArtifactRef(**raw["final_mask"])
            mask_path = private / "mask.png"
            mask_path.write_bytes(self.store.blob_path(mask).read_bytes())
            mask_path.chmod(0o400)
            parameters = next(s.parameters for s in plan.stages if s.stage_id == stage_id)
            built = build_image_asset(
                image_path=image_path,
                mask_path=mask_path,
                store_path=self.store.root,
                output_path=output,
                resolved_plan=shape_plan,
                seed=parameters["seed"],
                pipeline_type=parameters["pipeline_type"],
                run_id=attempt.child_run_id,
                child_context=context,
                input_binding=binding,
            )
            outputs = {
                "asset": built.asset_definition,
                "release": built.release_manifest,
                "glb": built.glb,
                "qa": built.quality_report,
            }
        if profile.identity_check is not None:
            profile.identity_check()
        self._complete(run.run_id, stage_id, outputs)

    def _validate_completed(self, run_id: str, stage_id: str, outputs: dict[str, Any]) -> None:
        run = self.repository.load(run_id)
        assert run.workbench is not None
        attempt = run.workbench.stage_states[stage_id].current()
        assert attempt.child_run_id and attempt.command_receipt
        child = self.repository.load(attempt.child_run_id)
        output = Path(attempt.command_receipt.output_location or "")
        if child.status != "succeeded" or not output.is_dir():
            raise ContractError("child success/publication evidence incomplete")
        published = json.loads((output / "run.json").read_text())
        if published != to_primitive(child) or child.parent_run_id != run_id:
            raise ContractError("published run does not match child")
        for value in outputs.values():
            if not isinstance(value, ArtifactRef) or not self.store.verify_digest(value):
                raise ContractError("child output missing or corrupt")
        plan = self._plan(run)
        profile = self.profiles[plan.backend_bindings["profile"]]
        if profile.identity_check is not None:
            profile.identity_check()
        if not profile.test_only:
            if stage_id == "propose":
                metadata = self.store.read_structured(outputs["proposals"])["backend_metadata"]
                for key in ("checkpoint_digest", "runner_digest"):
                    if metadata.get(key) != profile.proposal_identity[key]:
                        raise ContractError("SAM reported identity does not match plan")
            elif stage_id == "generate":
                release_data = self.store.read_structured(outputs["release"])
                records = [
                    self.store.read_structured(ArtifactRef(**ref))
                    for name, ref in release_data["files"].items()
                    if name.startswith("provenance/")
                ]
                generated_records = [r for r in records if r.get("operator") == "shape_generation"]
                if (
                    len(generated_records) != 1
                    or generated_records[0].get("model_digest")
                    != profile.shape_identity["model"]["snapshot_digest"]
                ):
                    raise ContractError("shape backend reported model identity does not match plan")
        if stage_id == "propose":
            files = {
                "proposals.json": outputs["proposals"],
                "provenance.json": outputs["provenance"],
            }
        elif stage_id == "select":
            binding_data = self.store.read_structured(outputs["binding"])
            files = {
                "selection.json": outputs["selection"],
                "image.input": ArtifactRef(**binding_data["image"]),
                "object_001.png": ArtifactRef(**binding_data["final_mask"]),
            }
        else:
            release = self.store.read_structured(outputs["release"])
            files = {name: ArtifactRef(**ref) for name, ref in release["files"].items()}
            if ArtifactRef(**release["asset_definition"]) != outputs["asset"]:
                raise ContractError("release asset does not match child output")
            files.update({"release.json": outputs["release"], "asset.json": outputs["asset"]})
        for name, ref in files.items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ContractError("unsafe child publication path")
            if (
                not self.store.verify_digest(ref)
                or (output / relative).read_bytes() != self.store.blob_path(ref).read_bytes()
            ):
                raise ContractError("published file is missing or differs from artifact")
        stage_plan = next(s for s in plan.stages if s.stage_id == stage_id)
        validate_operator_outputs(
            load_default_operator_specs()[f"{stage_plan.adapter}@{stage_plan.adapter_version}"],
            outputs,
            self.store,
        )

    def _complete(
        self, run_id: str, stage_id: str, outputs: dict[str, Any], *, restored: bool = False
    ) -> None:
        self._validate_completed(run_id, stage_id, outputs)
        run = self.repository.load(run_id)
        assert run.workbench is not None
        attempt = run.workbench.stage_states[stage_id].current()
        assert attempt.child_run_id is not None
        self._event(
            run_id,
            stage_id,
            ChildSucceeded(attempt.child_run_id, attempt.input_digest, outputs, True, restored),
        )
        self._prepare_next(run_id)

    def preview(
        self,
        run_id: str,
        expected_revision: int,
        proposal_id: str,
        invert: bool,
        keep_largest: bool,
    ) -> MaskDraft:
        with self._commands:
            run = self.repository.load(run_id)
            assert run.workbench is not None
            stage = run.workbench.stage_states["select"]
            if stage.status != "waiting_for_input" or stage.request_ref is None:
                raise ContractError("run is not waiting for mask input")
            request = decode_record(
                HumanInputRequest, self.store.read_structured(stage.request_ref)
            )
            raw = self.store.read_structured(request.input_refs["proposals"])
            indexed = {p["proposal_id"]: p for p in raw["proposals"]}
            mask = ArtifactRef(**indexed[proposal_id]["mask"])
            final, _, _ = prepare_selection_mask(
                self.store,
                request.input_refs["image"],
                mask,
                invert=invert,
                keep_largest=keep_largest,
            )
            draft = MaskDraft(proposal_id, final, invert, keep_largest)
            self._event(
                run_id,
                "select",
                MaskPreviewReady(stage.request_ref, draft),
                revision=expected_revision,
            )
            return draft

    def decision(
        self, run_id: str, expected_revision: int, idempotency_key: str, reviewer: str
    ) -> BuildRun:
        with self._commands:
            run = self.repository.load(run_id)
            assert run.workbench is not None
            stage = run.workbench.stage_states["select"]
            if stage.request_ref is None or stage.draft is None:
                raise ContractError("preview the selected mask before confirming")
            command = DecisionCommand(stage.request_ref, stage.draft, reviewer)
            receipt = self._receipt(
                run_id, "select", cache_key(command), idempotency_key, "decision"
            )
            command_ref = self.store.persist_structured(
                StructuredValue("quality_evidence", "DecisionCommand", "1.0", to_primitive(command))
            )
            # Command is immutable evidence. The transition binds it with the receipt.
            return self._event(
                run_id,
                "select",
                DecisionPrepared(command, receipt, command_ref),
                revision=expected_revision,
            )

    def retry(self, run_id: str, expected_revision: int, idempotency_key: str) -> BuildRun:
        with self._commands:
            run = self.repository.load(run_id)
            plan = self._plan(run)
            assert run.workbench is not None
            for prior in run.workbench.stage_states.values():
                for old in prior.attempts:
                    receipt = old.command_receipt
                    if receipt is not None and receipt.idempotency_key == idempotency_key:
                        if receipt.command_kind != "retry":
                            raise ContractError("idempotency key request conflict")
                        return run
            stage = next(
                (
                    run.workbench.stage_states[s.stage_id]
                    for s in plan.stages
                    if run.workbench.stage_states[s.stage_id].status in {"failed", "interrupted"}
                ),
                None,
            )
            if stage is None:
                raise ContractError("no retryable stage")
            inputs = self._resolved(
                run, next(s for s in plan.stages if s.stage_id == stage.stage_id), plan
            )
            if stage.human:
                previous = stage.current()
                command_ref = previous.resolved_inputs.get("decision_command")
                if not isinstance(command_ref, ArtifactRef):
                    raise ContractError("human retry requires the durable confirmed command")
                command = decode_record(DecisionCommand, self.store.read_structured(command_ref))
                return self._event(
                    run_id,
                    stage.stage_id,
                    RetryRequested(
                        inputs,
                        self._receipt(
                            run_id, stage.stage_id, cache_key(command), idempotency_key, "retry"
                        ),
                        None,
                        command,
                        command_ref,
                    ),
                    revision=expected_revision,
                    attempt=len(stage.attempts) + 1,
                )
            return self._event(
                run_id,
                stage.stage_id,
                RetryRequested(
                    inputs,
                    self._receipt(
                        run_id, stage.stage_id, inputs.digest(), idempotency_key, "retry"
                    ),
                ),
                revision=expected_revision,
                attempt=len(stage.attempts) + 1,
            )

    def recover(self, run_id: str) -> BuildRun:
        run = self.repository.load(run_id)
        self._plan(run)
        assert run.workbench is not None
        self.repository._sync_reference(run.workbench.plan_ref, set())
        for stage_id in run.workbench.stage_order:
            stage = run.workbench.stage_states[stage_id]
            if stage.status == "succeeded":
                self._validate_completed(run_id, stage_id, stage.current().outputs)
                stage_plan = next(s for s in self._plan(run).stages if s.stage_id == stage_id)
                expected_input = self._resolved(run, stage_plan, self._plan(run)).digest()
                if stage.human:
                    command_ref = stage.current().resolved_inputs.get("decision_command")
                    if not isinstance(command_ref, ArtifactRef):
                        raise ContractError("confirmed decision evidence missing")
                    command = decode_record(
                        DecisionCommand, self.store.read_structured(command_ref)
                    )
                    expected_input = cache_key(
                        {"request_input": expected_input, "decision": command}
                    )
                if expected_input != stage.current().input_digest:
                    raise ContractError("completed input digest changed")
                for value in stage.current().outputs.values():
                    if isinstance(value, ArtifactRef):
                        self.repository._sync_reference(value, set())
                continue
            if stage.status == "pending":
                self._prepare_next(run_id)
                break
            attempt = stage.current()
            if stage.request_ref is not None:
                self._verify_reference_closure(stage.request_ref, set())
                request = decode_record(
                    HumanInputRequest, self.store.read_structured(stage.request_ref)
                )
                if (request.run_id, request.stage_id) != (run_id, stage_id):
                    raise ContractError("human request ownership mismatch")
            if stage.draft is not None:
                self._verify_reference_closure(stage.draft.final_mask, set())
            if stage.status in {"running", "waiting_for_input", "failed", "interrupted"}:
                observation = None
                if attempt.worker_execution and self.probe:
                    if attempt.worker_execution.launch_phase != "prepared":
                        observation = self.probe.observe(attempt.worker_execution.identity())
                # Recovery never guesses success from loose artifact files.
                if attempt.child_run_id:
                    try:
                        child = self.repository.load(attempt.child_run_id)
                        if child.status == "succeeded":
                            outputs: dict[str, Any] = {}
                            for node in child.node_attempts:
                                outputs.update(node.outputs)
                            if stage_id == "propose":
                                outputs = {
                                    "proposals": outputs["proposals"],
                                    "provenance": outputs["provenance"],
                                }
                            elif stage_id == "select":
                                assert stage.draft is not None
                                selection = outputs["selection"]
                                proposals = attempt.resolved_inputs["proposals"]
                                assert isinstance(selection, ArtifactRef) and isinstance(
                                    proposals, ArtifactRef
                                )
                                outputs = {
                                    "selection": selection,
                                    "binding": create_selection_binding(
                                        self.store,
                                        original_proposals=proposals,
                                        selection=selection,
                                        draft=stage.draft,
                                    ),
                                }
                            else:
                                outputs = {
                                    "release": outputs["release"],
                                    "asset": outputs["asset"],
                                    "qa": outputs["report"],
                                    "glb": outputs["glb"],
                                }
                            self._complete(run_id, stage_id, outputs, restored=True)
                            break
                    except (FileNotFoundError, KeyError, ValueError):
                        pass
                self._event(run_id, stage_id, RecoveryObserved(observation))
            break
        return self.repository.load(run_id)

"""Single-writer asynchronous execution for the local DAG canvas.

The caller owns the repository context; close this service before releasing it.
GET projections never run recovery or wait for the engine's command lock.
"""

from __future__ import annotations

import io
import re
import threading
import uuid
import zipfile
from collections.abc import Callable
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PIL import Image

from .compiled_plan import CompiledPlan, thaw
from .contracts import ContractError, OperatorSpec, validate_port_value
from .dag_engine import DagEngine
from .dag_review import DagMaskReviewService, create_dag_review_server
from .models import ArtifactRef, PortValue
from .pipeline import _pipeline_from_raw, _port_spec, compile_pipeline, load_default_operator_specs
from .relations import RelationValidatorRegistry
from .serialization import cache_key, canonical_json_bytes, read_json, to_primitive
from .workbench_http import OutputPayload
from .workbench_persistence import CreationReceipt


def validate_editor_inputs(plan: CompiledPlan) -> str:
    """Return the supported input name after validating the entry contract."""
    if set(plan.inputs) == {"image"}:
        name = "image"
        kinds = tuple(plan.inputs[name].contract["kinds"])
        if kinds not in {("rgb_image",), ("rgba_image",)}:
            raise ContractError("canvas image input must have rgb_image or rgba_image kind")
        kind = kinds[0]
    elif set(plan.inputs) == {"observations"}:
        name, kind = "observations", "observation_bundle"
    else:
        raise ContractError(
            "canvas execution requires exactly one input named image or observations"
        )
    contract = plan.inputs[name].contract
    if tuple(contract["kinds"]) != (kind,):
        raise ContractError(f"canvas {name} input must have {kind} kind")
    if "artifact_ref" not in contract["carriers"] or contract["cardinality"] not in {
        "one",
        "zero_or_one",
    }:
        raise ContractError(f"canvas {name} input must accept a scalar ArtifactRef")
    fixed_schema = {
        "rgba_image": ("png", "1.0"),
        "observation_bundle": ("ObservationBundle", "1.0"),
    }.get(kind)
    if fixed_schema is not None:
        for field, expected in zip(("schema_name", "schema_version"), fixed_schema, strict=True):
            declared = contract.get(field)
            if declared is not None and declared != expected:
                raise ContractError(
                    f"canvas {name} input requires {field}={expected}; got {declared}"
                )
    return name


def validate_editor_execution(plan: CompiledPlan) -> str:
    """Shared creation gate: supported input contract and executable graph."""
    name = validate_editor_inputs(plan)
    if not plan.nodes:
        raise ContractError("canvas execution requires at least one processing node")
    return name


def validate_editor_reference_inputs(plan: CompiledPlan) -> None:
    """The named-reference endpoint accepts complete scalar ArtifactRef mappings."""
    if not plan.nodes:
        raise ContractError("canvas execution requires at least one processing node")
    for name, port in plan.inputs.items():
        contract = port.contract
        if "artifact_ref" not in contract["carriers"] or contract["cardinality"] not in {
            "one",
            "zero_or_one",
        }:
            raise ContractError(f"canvas {name} input must accept a scalar ArtifactRef")


class NodeEditorExecution:
    def __init__(
        self,
        engine: DagEngine,
        *,
        specs: dict[str, OperatorSpec] | None = None,
        relations: RelationValidatorRegistry | None = None,
    ) -> None:
        engine.repository._ready()
        self.engine = engine
        self.specs = specs if specs is not None else load_default_operator_specs()
        if relations is not None and relations is not engine.relations:
            raise ContractError("editor execution must share the engine relation registry")
        self.relations = engine.relations
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._active_run: str | None = None
        self._errors: dict[str, str] = {}
        self._closed = False
        self._review: tuple[DagMaskReviewService, ThreadingHTTPServer, threading.Thread] | None = (
            None
        )

    def _owned(self, run_id: str) -> None:
        if not re.fullmatch(r"dag_[A-Za-z0-9_-]{1,120}", run_id):
            raise ContractError("invalid DAG run ID")
        owner = read_json(self.engine.store.root / "parent_run_owners" / f"{run_id}.json")
        if owner.get("workbench_directory") != str(self.engine.repository.directory.resolve()):
            raise ContractError("run belongs to another editor directory")

    def plan(self, run_id: str) -> dict[str, Any]:
        """Read the verified persisted plan; never recover or dispatch a run."""
        self._owned(run_id)
        run = self.engine.repository.load(run_id)
        return self.engine._plan(run).to_dict()

    def draft_from_run(self, run_id: str) -> dict[str, Any]:
        """Project verified execution configuration into a new, unexecuted draft."""
        plan = self.plan(run_id)
        static = plan["static_plan"]
        nodes = {}
        for node in static["nodes"]:
            bound = plan["bindings"][node["node_id"]]
            inputs = {}
            for name, binding in node["inputs"].items():
                prefix = (
                    "pipeline.inputs"
                    if binding["source"] == "pipeline_input"
                    else f"{binding['node_id']}.outputs"
                )
                inputs[name] = f"{prefix}.{binding['port']}" + ("?" if binding["optional"] else "")
            nodes[node["node_id"]] = {
                "operator": node["operator"],
                "adapter": bound["adapter"],
                "inputs": inputs,
                "parameters": bound["parameters"],
                **({"backend": bound["backend"]} if bound.get("backend") else {}),
            }
        pipeline_inputs = {}
        for name, contract in static["inputs"].items():
            port = dict(contract)
            # The canvas uses kind for its supported single-kind pipeline inputs.
            if len(port["kinds"]) == 1:
                port["kind"] = port.pop("kinds")[0]
            pipeline_inputs[name] = port
        return {
            "source_run_id": run_id,
            "source_plan_id": plan["plan_id"],
            "pipeline": {
                "pipeline": static["pipeline_name"],
                "version": static["pipeline_version"],
                "inputs": pipeline_inputs,
                "nodes": nodes,
            },
        }

    def _viewable_output(self, ref: object) -> bool:
        if not isinstance(ref, ArtifactRef):
            return False
        return self.engine.store.get_manifest(ref.artifact_id).identity.kind in {
            "gltf_asset",
            "asset_release",
            "asset_definition",
            "quality_report",
            "rgb_image",
            "rgba_image",
        }

    def _run_busy(self, run_id: str) -> bool:
        review = self._review
        return bool(
            (self._active_run == run_id and self._worker and self._worker.is_alive())
            or (
                review
                and review[0].run_id == run_id
                and review[0]._thread
                and review[0]._thread.is_alive()
            )
        )

    def snapshot(self, run_id: str) -> dict[str, Any]:
        self._owned(run_id)
        # A worker can publish its final snapshot and exit while this read is in
        # flight. Never label the earlier snapshot idle; let the next poll reload.
        busy_before = self._run_busy(run_id)
        run = self.engine.repository.load(run_id)
        if run.dag is None:
            raise ContractError("not a DAG run")
        review = self._review
        return {
            "run": to_primitive(run),
            "outputs": [
                {
                    "node_id": state.node_id,
                    "port": port,
                    "kind": self.engine.store.get_manifest(ref.artifact_id).identity.kind,
                    "url": f"/api/runs/{run_id}/outputs/{state.node_id}/{port}",
                }
                for state in run.dag.node_states.values()
                if state.status == "succeeded"
                for port, ref in state.current().outputs.items()
                if isinstance(ref, ArtifactRef) and self._viewable_output(ref)
            ],
            "busy": busy_before or self._run_busy(run_id),
            "error": self._errors.get(run_id)
            or (review[0]._error if review and review[0].run_id == run_id else None),
        }

    def output_reference(self, run_id: str, node_id: str, port: str) -> dict[str, Any]:
        """Verify an owned output before offering its exact identity for a new run."""
        self._owned(run_id)
        run = self.engine.repository.load(run_id)
        if run.dag is None or run.dag.node_states[node_id].status != "succeeded":
            raise ContractError("output reference requires a successful node")
        reference = run.dag.node_states[node_id].current().outputs[port]
        if not isinstance(reference, ArtifactRef):
            raise ContractError("output reference requires a scalar ArtifactRef")
        self.engine.repository.verify_reference_closure(reference)
        identity = self.engine.store.get_manifest(reference.artifact_id).identity
        return {
            "source_run_id": run_id,
            "node_id": node_id,
            "port": port,
            "reference": to_primitive(reference),
            "kind": identity.kind,
            "schema_name": identity.schema_name,
            "schema_version": identity.schema_version,
        }

    def output(self, run_id: str, node_id: str, port: str) -> OutputPayload:
        self._owned(run_id)
        run = self.engine.repository.load(run_id)
        if run.dag is None:
            raise ContractError("unsupported output")
        state = run.dag.node_states[node_id]
        if state.status != "succeeded":
            raise ContractError("output requires a successful node")
        ref = state.current().outputs[port]
        if not isinstance(ref, ArtifactRef) or not self._viewable_output(ref):
            raise ContractError("unsupported output artifact kind")
        self.engine.repository.verify_reference_closure(ref)
        media = self.engine.store.get_manifest(ref.artifact_id).identity.identity_metadata.get(
            "media_type", "application/octet-stream"
        )
        return OutputPayload(self.engine.store.blob_path(ref).read_bytes(), str(media))

    def release_archive(self, run_id: str, node_id: str, port: str) -> OutputPayload:
        """Download declared release files; never re-export or regenerate evidence."""
        from .release_io import release_files as _release_files

        self._owned(run_id)
        run = self.engine.repository.load(run_id)
        if run.dag is None or run.dag.node_states[node_id].status != "succeeded":
            raise ContractError("archive requires a successful release node")
        ref = run.dag.node_states[node_id].current().outputs[port]
        store = self.engine.store
        if (
            not isinstance(ref, ArtifactRef)
            or store.get_manifest(ref.artifact_id).identity.kind != "asset_release"
        ):
            raise ContractError("archive requires an AssetRelease")
        self.engine.repository.verify_reference_closure(ref)
        release = store.read_structured(ref)
        files = _release_files(store, ref)
        files["asset.json"] = ArtifactRef(**release["asset_definition"])
        files["release.json"] = ref
        # Bound the in-memory download independently of compression ratio.
        limit = 512 * 1024 * 1024
        if sum(store.blob_path(item).stat().st_size for item in files.values()) > limit:
            raise ContractError("release archive exceeds 512 MiB download limit")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, item in sorted(files.items()):
                archive.writestr(zipfile.ZipInfo(name), store.blob_path(item).read_bytes())
        return OutputPayload(buffer.getvalue(), "application/zip")

    def list_runs(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted((self.engine.store.root / "parent_run_owners").glob("dag_*.json")):
            try:
                self._owned(path.stem)
            except (OSError, ValueError, KeyError):
                continue
            try:
                run = self.engine.repository.load(path.stem)
                if run.dag is not None:
                    rows.append({"run_id": run.run_id, "status": run.status})
            except (OSError, ValueError, KeyError) as error:
                rows.append(
                    {"run_id": path.stem, "status": "recovery_blocked", "error": str(error)}
                )
        return rows

    def _stop_review(
        self, *, wait: bool = False, validate: Callable[[], Any] | None = None
    ) -> None:
        if self._review is None:
            if validate is not None:
                validate()
            return
        service, server, thread = self._review
        # Coordinate with decision() itself: no decision can pass its admission
        # check after closing, even if a request raced with a canvas command.
        with service._lock:
            if not wait and service._thread and service._thread.is_alive():
                raise ContractError("a human decision is still executing")
            if validate is not None:
                validate()
            service._closed = True
        server.shutdown()
        server.server_close()
        thread.join()
        service.close()
        self._review = None

    def _idle(self) -> None:
        if self._closed:
            raise RuntimeError("editor execution service is closing")
        if self._worker and self._worker.is_alive():
            raise ContractError("another editor command is still executing")
        review = self._review
        if review is not None:
            with review[0]._lock:
                if review[0]._thread and review[0]._thread.is_alive():
                    raise ContractError("a human decision is still executing")

    def _dispatch(self, run_id: str, command: Callable[[], Any]) -> None:
        # All routes (including resume/retry after a pre-marker crash) must
        # establish creation evidence before any execution can occur.
        repository = self.engine.repository
        marker = repository.directory / "created_runs" / f"{run_id}.json"
        if not marker.exists():
            repository._mutate(
                lambda: repository.io.write(
                    marker,
                    canonical_json_bytes({"run_id": run_id}),
                    exclusive=True,
                )
            )
        self._errors.pop(run_id, None)
        self._active_run = run_id

        def execute() -> None:
            try:
                command()
            except Exception as error:
                self._errors[run_id] = f"{type(error).__name__}: {error}"

        self._worker = threading.Thread(target=execute, daemon=True)
        self._worker.start()

    def upload_image(self, data: bytes, *, rgba: bool = False) -> dict[str, Any]:
        """Import a decoded image without creating a run or dispatching inference."""
        if not 0 < len(data) <= 20 * 1024 * 1024:
            raise ContractError("image upload must be at most 20 MiB")
        try:
            decoded = Image.open(io.BytesIO(data))
        except Image.DecompressionBombError as error:
            raise ContractError("upload must be at most 25 megapixels") from error
        with decoded as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise ContractError("upload must be PNG, JPEG or WebP")
            if image.width * image.height > 25_000_000 or getattr(image, "n_frames", 1) != 1:
                raise ContractError("upload must be a single image of at most 25 megapixels")
            image.load()
            if rgba and (image.format != "PNG" or image.mode != "RGBA"):
                raise ContractError("prepared RGBA input must be an RGBA PNG")
            if rgba and image.getchannel("A").getextrema()[1] == 0:
                raise ContractError("prepared RGBA input contains no foreground")
            metadata = {
                "media_type": Image.MIME[image.format],
                "width": image.width,
                "height": image.height,
                "channel_layout": image.mode,
            }
        with self._lock:
            if self._closed:
                raise ContractError("editor execution service is closing")
            ref = self.engine.store.persist_bytes(
                data,
                kind="rgba_image" if rgba else "rgb_image",
                schema_name="png" if rgba else "raster_image",
                schema_version="1.0",
                identity_metadata={"media_type": "image/png", "channel_layout": "RGBA"}
                if rgba
                else metadata,
            )
        return {"image_ref": to_primitive(ref)}

    def upload_mask(self, data: bytes) -> dict[str, Any]:
        """Import a strict binary PNG mask without creating a run."""
        if not 0 < len(data) <= 20 * 1024 * 1024:
            raise ContractError("mask upload must be at most 20 MiB")
        try:
            decoded = Image.open(io.BytesIO(data))
        except Image.DecompressionBombError as error:
            raise ContractError("mask upload must be at most 25 megapixels") from error
        with decoded as mask:
            if mask.format != "PNG" or mask.width * mask.height > 25_000_000:
                raise ContractError("mask upload must be a PNG of at most 25 megapixels")
            if getattr(mask, "n_frames", 1) != 1:
                raise ContractError("mask upload must be a single frame")
            mask.load()
            if mask.mode not in {"1", "L"} or "transparency" in mask.info:
                raise ContractError("mask upload must be a grayscale PNG without transparency")
            values = set(mask.convert("L").tobytes())
            if not values <= {0, 255} or 255 not in values:
                raise ContractError("mask upload must contain only 0/255 values and foreground")
            width, height = mask.size
            channel_layout = mask.mode
        with self._lock:
            if self._closed:
                raise ContractError("editor execution service is closing")
            ref = self.engine.store.persist_bytes(
                data,
                kind="binary_mask",
                schema_name="png",
                schema_version="1.0",
                identity_metadata={
                    "media_type": "image/png",
                    "channel_layout": channel_layout,
                    "width": width,
                    "height": height,
                },
            )
        return {"mask_ref": to_primitive(ref)}

    def import_observations(self, images: list[dict[str, Any]]) -> dict[str, Any]:
        """Assemble explicit ordered RGB references, without estimating cameras."""
        from .models import ObservationView
        from .observations import make_observation_bundle, observation_bundle_value

        if not isinstance(images, list) or not 2 <= len(images) <= 32:
            raise ContractError("select between 2 and 32 RGB images")
        refs = []
        for item in images:
            if (
                not isinstance(item, dict)
                or set(item) != {"artifact_id"}
                or not isinstance(item["artifact_id"], str)
            ):
                raise ContractError("images must contain ArtifactRef objects")
            refs.append(ArtifactRef(**item))
        if len({ref.artifact_id for ref in refs}) != len(refs):
            raise ContractError("observation images must be distinct")
        with self._lock:
            if self._closed:
                raise ContractError("editor execution service is closing")
            bundle = make_observation_bundle(
                [ObservationView(f"view_{index:03d}", ref) for index, ref in enumerate(refs)],
                self.engine.store,
            )
            reference = self.engine.store.persist_structured(observation_bundle_value(bundle))
            return {
                "observations_ref": to_primitive(reference),
                "views": to_primitive(bundle.views),
            }

    def start(
        self,
        raw: dict[str, Any],
        image_path: str | None = None,
        *,
        image_ref: dict[str, Any] | None = None,
        observations_ref: dict[str, Any] | None = None,
        input_refs: dict[str, dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if idempotency_key is not None and (
                not isinstance(idempotency_key, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", idempotency_key)
            ):
                raise ContractError("invalid creation idempotency key")
            if self._closed:
                raise ContractError("editor execution service is closing")
            if not isinstance(raw, dict) or set(raw) - {"pipeline", "version", "inputs", "nodes"}:
                raise ContractError("invalid pipeline object")
            plan = self.engine.registry.bind_plan(
                compile_pipeline(
                    _pipeline_from_raw(raw),
                    self.specs,
                    relation_registry=self.relations,
                    require_explicit_joins=True,
                ),
                relation_registry=self.relations,
            )
            actual_inputs: dict[str, PortValue | list[PortValue]]
            if input_refs is not None:
                if any(value is not None for value in (image_path, image_ref, observations_ref)):
                    raise ContractError("multi-input refs cannot be combined with image sources")
                validate_editor_reference_inputs(plan.static_plan)
                name = None
                if not isinstance(input_refs, dict) or set(input_refs) != set(
                    plan.static_plan.inputs
                ):
                    raise ContractError(
                        "multi-input run requires one ArtifactRef for every pipeline input"
                    )
                if any(
                    not isinstance(value, dict) or set(value) != {"artifact_id"}
                    for value in input_refs.values()
                ):
                    raise ContractError("multi-input inputs must be ArtifactRef objects")
                actual_inputs = {key: ArtifactRef(**value) for key, value in input_refs.items()}
            else:
                name = validate_editor_execution(plan.static_plan)
                if sum(v is not None for v in (image_path, image_ref, observations_ref)) != 1:
                    raise ContractError("provide exactly one input source")
                if (name == "observations") != (observations_ref is not None):
                    raise ContractError("input source does not match pipeline input")
                reference = observations_ref if name == "observations" else image_ref
                if reference is not None:
                    if not isinstance(reference, dict) or set(reference) != {"artifact_id"}:
                        raise ContractError("input reference must be an ArtifactRef")
                    image = ArtifactRef(**reference)
                else:
                    if not isinstance(image_path, str) or not image_path.strip():
                        raise ContractError("image path is required")
                    source_path = Path(image_path).expanduser()
                    if source_path.stat().st_size > 20 * 1024 * 1024:
                        raise ContractError("image upload must be at most 20 MiB")
                    contract = plan.static_plan.inputs[name].contract
                    image = ArtifactRef(
                        **self.upload_image(
                            source_path.read_bytes(),
                            rgba=tuple(contract["kinds"]) == ("rgba_image",),
                        )["image_ref"]
                    )
                actual_inputs = {name: image}
            for input_name, value in actual_inputs.items():
                assert isinstance(value, ArtifactRef)
                contract = plan.static_plan.inputs[input_name].contract
                validate_port_value(
                    operator=plan.static_plan.pipeline_name,
                    port_name=input_name,
                    spec=_port_spec(thaw(contract)),
                    value=value,
                    store=self.engine.store,
                )
                identity = self.engine.store.get_manifest(value.artifact_id).identity
                self.engine.repository.verify_reference_closure(value)
                if identity.kind == "observation_bundle":
                    from .observations import observation_bundle_from_artifact

                    observation_bundle_from_artifact(value, self.engine.store)
                if identity.kind == "rgba_image":
                    if (
                        identity.schema_name,
                        identity.schema_version,
                        identity.identity_metadata,
                    ) != ("png", "1.0", {"media_type": "image/png", "channel_layout": "RGBA"}):
                        raise ContractError("prepared RGBA Artifact has an invalid identity")
                    blob = self.engine.store.blob_path(value)
                    if blob.stat().st_size > 20 * 1024 * 1024:
                        raise ContractError("image upload must be at most 20 MiB")
                    with Image.open(blob) as prepared:
                        if (
                            prepared.format != "PNG"
                            or prepared.mode != "RGBA"
                            or prepared.width * prepared.height > 25_000_000
                            or getattr(prepared, "n_frames", 1) != 1
                        ):
                            raise ContractError("prepared RGBA input must be a bounded RGBA PNG")
                        prepared.load()
                        if prepared.getchannel("A").getextrema()[1] == 0:
                            raise ContractError("prepared RGBA input contains no foreground")
            run_id = None
            marker = None
            repository = self.engine.repository
            if idempotency_key is not None:
                if repository.creation_receipt("node-editor:" + idempotency_key) is None:
                    self._idle()
                receipt = repository.reserve_creation(
                    CreationReceipt(
                        "node-editor:" + idempotency_key,
                        cache_key({"plan": plan.to_dict(), "inputs": to_primitive(actual_inputs)}),
                        f"dag_{uuid.uuid4().hex}",
                    )
                )
                run_id = receipt.run_id
                marker = repository.directory / "created_runs" / f"{run_id}.json"
                index = self.engine.store.root / "runs" / f"{run_id}.json"
                if index.exists():
                    existing = repository.load(run_id)
                    if (
                        existing.dag is None
                        or existing.dag.plan_id != plan.plan_id
                        or existing.dag.named_actual_inputs != actual_inputs
                    ):
                        raise ContractError("creation receipt does not match persisted run")
                    if not marker.exists():
                        repository._mutate(
                            lambda: repository.io.write(
                                marker,
                                canonical_json_bytes({"run_id": run_id}),
                                exclusive=True,
                            )
                        )
                    # No resume/dispatch on repeated POST, even after restart.
                    return self.snapshot(run_id)
                if marker.exists():
                    raise ContractError("created run index is missing; refusing to recreate it")
            self._idle()
            self._stop_review()
            run = self.engine.create(plan, actual_inputs, run_id=run_id)
            if marker is not None:
                repository._mutate(
                    lambda: repository.io.write(
                        marker,
                        canonical_json_bytes({"run_id": run.run_id}),
                        exclusive=True,
                    )
                )
            self._dispatch(run.run_id, lambda: self.engine.drain(run.run_id))
            return self.snapshot(run.run_id)

    def _revision(self, run_id: str, expected_revision: int) -> None:
        self._owned(run_id)
        run = self.engine.repository.load(run_id)
        if (
            run.dag is None
            or type(expected_revision) is not int
            or run.dag.revision != expected_revision
        ):
            raise ContractError("DAG revision conflict")

    def resume(self, run_id: str, expected_revision: int) -> dict[str, Any]:
        with self._lock:
            self._idle()
            self._revision(run_id, expected_revision)
            self._stop_review(validate=lambda: self._revision(run_id, expected_revision))
            self._dispatch(run_id, lambda: self.engine.drain(run_id))
            return self.snapshot(run_id)

    def retry(self, run_id: str, node_id: str, expected_revision: int) -> dict[str, Any]:
        with self._lock:
            self._idle()
            self._revision(run_id, expected_revision)
            run = self.engine.repository.load(run_id)
            assert run.dag is not None
            if node_id not in run.dag.node_states or run.dag.node_states[node_id].status not in {
                "failed",
                "interrupted",
                "recovery_blocked",
            }:
                raise ContractError("node is not retryable")
            self._stop_review(validate=lambda: self._revision(run_id, expected_revision))
            self._dispatch(run_id, lambda: self.engine.retry(run_id, node_id, expected_revision))
            return self.snapshot(run_id)

    def review(self, run_id: str, node_id: str) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("editor execution service is closing")
            if self._review and (self._review[0].run_id, self._review[0].node_id) == (
                run_id,
                node_id,
            ):
                return {"url": f"http://127.0.0.1:{self._review[1].server_port}/"}
            self._idle()
            self._owned(run_id)

            def validate_review() -> None:
                run = self.engine.repository.load(run_id)
                if run.dag is None or node_id not in run.dag.node_states:
                    raise ContractError("unknown review node")
                state = run.dag.node_states[node_id]
                if not state.attempts or state.current().request is None:
                    raise ContractError("node has no human request")
                plan = self.engine._plan(run)
                if plan.bindings[node_id].adapter != "image_mask_selection@1":
                    raise ContractError("review requires the image_mask_selection adapter")
                node = next(item for item in plan.static_plan.nodes if item.node_id == node_id)
                self.engine._human_evidence(run, plan, node, state.current())
                request_ref = state.current().request
                assert request_ref is not None
                request = self.engine.store.read_structured(request_ref)
                if request.get("decision_contract") != "single-proposal-mask-edit@1" or request.get(
                    "proposals"
                ) != to_primitive(state.current().resolved_inputs.get("proposals")):
                    raise ContractError("node is not an exact mask review request")
                proposals = ArtifactRef(**request["proposals"])
                self.engine.repository.verify_reference_closure(proposals)

            self._stop_review(validate=validate_review)
            service = DagMaskReviewService(self.engine, run_id, node_id)
            server = create_dag_review_server(service, 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            self._review = service, server, thread
            thread.start()
            return {"url": f"http://127.0.0.1:{server.server_port}/"}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._stop_review(wait=True)
            if self._worker:
                self._worker.join()

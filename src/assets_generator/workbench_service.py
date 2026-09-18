"""Workbench HTTP projection; durable business state remains in BuildRun."""

from __future__ import annotations

import io
import threading
from typing import Any
from urllib.parse import quote

from PIL import Image

from .models import ArtifactRef
from .serialization import read_json, to_primitive
from .workbench_engine import WorkbenchEngine
from .workbench_http import OutputPayload


class LocalWorkbenchService:
    def __init__(self, engine: WorkbenchEngine) -> None:
        self.engine = engine
        self._threads: list[threading.Thread] = []
        self._background_error: str | None = None
        self._closed = False
        self._recovery_errors: dict[str, str] = {}

    def recover_runs(self) -> None:
        """Quarantine an invalid run without denying access to other local runs."""
        for row in self.list_runs():
            run_id = row["run_id"]
            if run_id in self._recovery_errors:
                continue
            try:
                self.engine.recover(run_id)
            except Exception as error:
                self._recovery_errors[run_id] = f"{type(error).__name__}: {error}"

    def _check_recovery(self, run_id: str) -> None:
        if run_id in self._recovery_errors:
            raise RuntimeError(f"run recovery blocked: {self._recovery_errors[run_id]}")

    def resume(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        if self._closed:
            raise RuntimeError("workbench service is closing")

        # drain's serial lock prevents parallel GPU operations even across new commands.
        def run() -> None:
            try:
                self.engine.drain()
            except Exception as error:
                self._background_error = str(error)

        worker = threading.Thread(target=run, daemon=True)
        self._threads.append(worker)
        worker.start()

    def close(self) -> None:
        self._closed = True
        for worker in self._threads:
            worker.join()

    def _owned(self, run_id: str) -> None:
        if not run_id.startswith("run_") or not all(c.isalnum() or c in "_-" for c in run_id):
            raise ValueError("invalid run ID")
        owner = read_json(self.engine.store.root / "parent_run_owners" / f"{run_id}.json")
        if owner["workbench_directory"] != str(self.engine.repository.directory.resolve()):
            raise ValueError("run belongs to another workbench")

    def catalog(self) -> dict[str, Any]:
        return {
            "template": "photo_object_asset@1",
            "backends": [
                {"id": key, "label": key, "test_only": profile.test_only}
                for key, profile in self.engine.profiles.items()
            ],
        }

    def import_image(self, normalized_png: bytes) -> dict[str, Any]:
        with Image.open(io.BytesIO(normalized_png)) as image:
            if image.mode != "RGB":
                raise ValueError("normalized image must be RGB")
            width, height = image.size
        reference = self.engine.store.persist_bytes(
            normalized_png,
            kind="rgb_image",
            schema_name="raster_image",
            schema_version="1.0",
            identity_metadata={
                "media_type": "image/png",
                "width": width,
                "height": height,
                "channel_layout": "RGB",
            },
        )
        return {"image": to_primitive(reference)}

    def list_runs(self) -> list[dict[str, Any]]:
        result = []
        for path in sorted((self.engine.store.root / "parent_run_owners").glob("*.json")):
            try:
                owner = read_json(path)
            except (OSError, ValueError):
                continue  # A corrupt ownership marker cannot establish access to a run.
            if owner.get("workbench_directory") != str(self.engine.repository.directory.resolve()):
                continue
            try:
                if path.stem in self._recovery_errors:
                    result.append(self.get_run(path.stem))
                    continue
                run = self.engine.repository.load(path.stem)
                if run.workbench is not None:
                    result.append({"run_id": run.run_id, "status": run.status})
            except FileNotFoundError as error:
                if not (self.engine.store.root / "runs" / path.name).exists():
                    continue  # Reserved creation has no first snapshot yet.
                self._recovery_errors[path.stem] = f"{type(error).__name__}: {error}"
                result.append(self.get_run(path.stem))
            except Exception as error:
                self._recovery_errors[path.stem] = f"{type(error).__name__}: {error}"
                result.append(self.get_run(path.stem))
        return result

    def create_run(self, body: dict[str, Any]) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("workbench service is closing")
        if set(body) != {"image", "shape_profile", "parameters", "idempotency_key"}:
            raise ValueError("create requires image, shape_profile, parameters and idempotency_key")
        run = self.engine.create(
            ArtifactRef(**body["image"]),
            body["shape_profile"],
            body["parameters"],
            body["idempotency_key"],
        )
        self._check_recovery(run.run_id)
        self._dispatch()
        return self.get_run(run.run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        self._owned(run_id)
        if run_id in self._recovery_errors:
            return {
                "run_id": run_id,
                "status": "recovery_blocked",
                "recovery_error": self._recovery_errors[run_id],
                "stages": [],
                "outputs": [],
                "qa": [],
            }
        run = self.engine.repository.load(run_id)
        if run.workbench is None:
            raise ValueError("not a workbench run")
        base = f"/runs/{quote(run_id, safe='')}/outputs/"
        view: dict[str, Any] = {
            "run_id": run_id,
            "status": run.status,
            "revision": run.workbench.state_revision,
            "image_url": base + "image",
            "stages": [],
            "outputs": [],
            "qa": [],
        }
        for stage_id in run.workbench.stage_order:
            stage = run.workbench.stage_states[stage_id]
            current = stage.current() if stage.attempts else None
            view["stages"].append(
                {
                    "stage_id": stage_id,
                    "status": stage.status,
                    "error": current.error_code if current else None,
                    "execution_mode": current.execution_mode if current else None,
                }
            )
        selection = run.workbench.stage_states["select"]
        proposed = run.workbench.stage_states["propose"]
        if proposed.status == "succeeded":
            ref = proposed.current().outputs["proposals"]
            assert isinstance(ref, ArtifactRef)
            proposals = self.engine.store.read_structured(ref)
            review: dict[str, Any] = {
                "confirmed": selection.status == "succeeded",
                "items": [
                    {
                        "proposal_id": item["proposal_id"],
                        "area": item["area"],
                        "mask_url": base + f"mask_{index}",
                    }
                    for index, item in enumerate(proposals["proposals"])
                ],
            }
            if selection.draft:
                review["draft"] = {**to_primitive(selection.draft), "mask_url": base + "preview"}
            view["review"] = review
        generated = run.workbench.stage_states["generate"]
        if generated.status == "succeeded":
            view["outputs"] = [
                {"key": key, "label": key, "url": base + key}
                for key in ("glb", "release", "asset", "qa")
            ]
            qa = generated.current().outputs["qa"]
            assert isinstance(qa, ArtifactRef)
            report = self.engine.store.read_structured(qa)
            view["qa"] = [
                {
                    "check": item.get("check_id", item.get("name", "unknown")),
                    "status": item["status"],
                }
                for item in report.get("checks", [])
            ]
        if self._background_error:
            view["background_error"] = self._background_error
        return view

    def command(self, run_id: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("workbench service is closing")
        self._owned(run_id)
        self._check_recovery(run_id)
        revision = body.get("expected_revision")
        if type(revision) is not int:
            raise ValueError("expected_revision must be an integer")
        if action == "mask-preview":
            if set(body) != {"expected_revision", "proposal_id", "invert", "keep_largest"}:
                raise ValueError("invalid mask preview fields")
            self.engine.preview(
                run_id, revision, body["proposal_id"], body["invert"], body["keep_largest"]
            )
        elif action == "decision":
            if set(body) != {"expected_revision", "idempotency_key", "reviewer"}:
                raise ValueError("invalid decision fields")
            self.engine.decision(run_id, revision, body["idempotency_key"], body["reviewer"])
        elif action == "retry":
            if set(body) != {"expected_revision", "idempotency_key"}:
                raise ValueError("invalid retry fields")
            self.engine.retry(run_id, revision, body["idempotency_key"])
        else:
            raise ValueError("unsupported command")
        self._dispatch()
        return self.get_run(run_id)

    def output(self, run_id: str, key: str) -> OutputPayload:
        self._owned(run_id)
        self._check_recovery(run_id)
        run = self.engine.repository.load(run_id)
        if run.workbench is None:
            raise ValueError("not a workbench run")
        reference: ArtifactRef
        if key == "image":
            image = run.inputs["image"]
            assert isinstance(image, ArtifactRef)
            reference = image
        elif key.startswith("mask_"):
            if not key.removeprefix("mask_").isdigit():
                raise ValueError("invalid mask index")
            stage = run.workbench.stage_states["propose"]
            if stage.status != "succeeded":
                raise ValueError("proposals unavailable")
            proposal_ref = stage.current().outputs["proposals"]
            assert isinstance(proposal_ref, ArtifactRef)
            raw = self.engine.store.read_structured(proposal_ref)
            reference = ArtifactRef(**raw["proposals"][int(key.removeprefix("mask_"))]["mask"])
        elif key == "preview":
            draft = run.workbench.stage_states["select"].draft
            if draft is None:
                raise ValueError("preview unavailable")
            reference = draft.final_mask
        elif key in {"asset", "release", "glb", "qa"}:
            generated = run.workbench.stage_states["generate"]
            if generated.status != "succeeded":
                raise ValueError("output unavailable")
            output = generated.current().outputs[key]
            assert isinstance(output, ArtifactRef)
            reference = output
        else:
            raise ValueError("unknown output key")
        if not self.engine.store.verify_digest(reference):
            raise ValueError("output is missing or corrupt")
        identity = self.engine.store.get_manifest(reference.artifact_id).identity
        media_type = str(identity.identity_metadata.get("media_type", "application/octet-stream"))
        return OutputPayload(self.engine.store.blob_path(reference).read_bytes(), media_type)

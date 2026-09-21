"""Explicit SAM3D executor behind the existing durable remote job service.

Start claims once and submits once. Recover only looks up/queries the original
upstream key. Input blobs and output files are fixed in the outer SQLite store.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .artifact_store import LocalArtifactStore
from .remote_protocol import RemoteIdentity, RemoteJob, RemoteRequest
from .sam3d_http import (
    Sam3DClient,
    Sam3DConflict,
    Sam3DRejected,
    Sam3DUnknown,
    validate_images,
    validate_options,
)
from .sam3d_result import REQUIRED_FILES, verify_result
from .sam3d_service_store import InvalidSam3DInput, Sam3DServiceStore
from .serialization import canonical_json_bytes, sha256_bytes, to_primitive


class Sam3DBridge:
    def __init__(self, client: Sam3DClient, deployment: dict[str, Any]):
        # A fresh value is not enough: profiles must pin the audited deployment.
        self.client = client
        self._deployment_json = canonical_json_bytes(deployment)
        digest = deployment.get("backend_digest")
        if not isinstance(digest, str):
            raise ValueError("SAM3D bridge requires pinned deployment identity")
        self.backend_digest = digest

    def identity(self, service_id: str) -> RemoteIdentity:
        """Pin both the upstream declaration and compatibility implementation."""
        modules = (
            "sam3d_bridge.py",
            "sam3d_http.py",
            "sam3d_result.py",
            "sam3d_service_store.py",
            "remote_shape_output.py",
            "remote_protocol.py",
            "remote_service_store.py",
            "mesh_io.py",
        )
        body = {
            "schema": "sam3d-bridge@1",
            "deployment": self.deployment,
            "source": {
                name: sha256_bytes(Path(__file__).with_name(name).read_bytes()) for name in modules
            },
        }
        return RemoteIdentity(service_id, sha256_bytes(canonical_json_bytes(body)))

    @property
    def deployment(self) -> dict[str, Any]:
        return json.loads(self._deployment_json)  # type: ignore[no-any-return]

    def _intent(self, owner: Sam3DServiceStore, request: RemoteRequest) -> dict[str, Any]:
        if owner.identity != self.identity(owner.identity.service_id):
            raise ValueError("SAM3D compatibility implementation/profile identity mismatch")
        payload = json.loads(request.payload_json)
        if set(payload) != {
            "operation",
            "image_digest",
            "mask_digest",
            "parameters",
            "backend_digest",
        }:
            raise ValueError("SAM3D bridge payload fields differ")
        if (
            payload["operation"] != "masked_shape_generation@1"
            or payload["backend_digest"] != self.backend_digest
        ):
            raise ValueError("SAM3D bridge operation/deployment mismatch")
        image, mask = (
            owner.get_blob(payload["image_digest"]),
            owner.get_blob(payload["mask_digest"]),
        )
        validate_images(image, mask)
        identity = {
            "schema": "sam3d-request@1",
            "image": payload["image_digest"],
            "mask": payload["mask_digest"],
            "points": None,
            "options": validate_options(payload["parameters"]),
        }
        return {
            "endpoint": self.client.endpoint,
            "submission_key": request.submission_key,
            "outer_request_digest": request.request_digest,
            "request_digest": sha256_bytes(canonical_json_bytes(identity)),
            "request_identity": identity,
            "backend_digest": self.backend_digest,
            "deployment": self.deployment,
        }

    def _check_deployment(self) -> None:
        raw = self.client.capabilities()
        if raw.get("protocol_version") != "1.1" or raw.get("deployment") != self.deployment:
            raise Sam3DUnknown("SAM3D deployment differs from fixed profile")
        if (
            raw.get("idempotency", {}).get("persistent") is not True
            or raw.get("idempotency", {}).get("lookup_by_key") is not True
        ):
            raise Sam3DUnknown("SAM3D durable lookup unavailable")

    def start_next(
        self, owner: Sam3DServiceStore, artifacts: LocalArtifactStore
    ) -> RemoteJob | None:
        if owner.identity != self.identity(owner.identity.service_id):
            raise ValueError("SAM3D compatibility implementation/profile identity mismatch")
        self._check_deployment()

        def validate(request: RemoteRequest) -> dict[str, Any]:
            try:
                return self._intent(owner, request)
            except (ValueError, TypeError, KeyError, OSError) as error:
                raise InvalidSam3DInput("invalid SAM3D inputs") from error

        request = owner.claim_authorized(validate)
        if request is None:
            return None
        job = owner.lookup(request)
        if job is not None and job.state == "failed":
            return job
        intent = owner.bridge_record(request)["intent"]
        try:
            job_id = self.client.submit_once(
                owner.get_blob(intent["request_identity"]["image"]),
                owner.get_blob(intent["request_identity"]["mask"]),
                intent["request_identity"]["options"],
                submission_key=intent["submission_key"],
                backend_digest=self.backend_digest,
            )
        except Sam3DConflict:
            # A conflicting key may already name an existing task. Never release its slot.
            raise Sam3DUnknown("SAM3D submission conflict; reconcile original key") from None
        except Sam3DRejected:
            return owner.reject_submission(request, "SAM3D_REJECTED")
        owner.fix_evidence(
            request,
            "receipt",
            {
                "job_id": job_id,
                "submission_key": request.submission_key,
                "request_digest": intent["request_digest"],
                "backend_digest": self.backend_digest,
            },
        )
        return self.recover(owner, request, artifacts)

    def recover(
        self,
        owner: Sam3DServiceStore,
        request: RemoteRequest,
        artifacts: LocalArtifactStore,
    ) -> RemoteJob:
        job = owner.lookup(request)
        if job is None or job.state == "queued":
            raise ValueError("SAM3D recovery requires original claimed job")
        record = owner.bridge_record(request)
        intent = record["intent"]
        local_failure = (record["completion"] or {}).get("local_failure")
        if local_failure is not None:
            if (
                job.state != "failed"
                or record["receipt"] is not None
                or json.loads(job.error_json or b"{}") != local_failure
                or intent.get("outer_request_digest") != request.request_digest
                or local_failure.get("code") not in {"SAM3D_INVALID_INPUT", "SAM3D_REJECTED"}
            ):
                raise ValueError("SAM3D local failure evidence inconsistent")
            return job
        if intent != self._intent(owner, request):
            raise Sam3DUnknown("SAM3D intent no longer matches original inputs/profile")
        if job.state in {"succeeded", "failed"}:
            expected_status = "completed" if job.state == "succeeded" else "failed"
            if (
                record["receipt"] is None
                or record["completion"] is None
                or record["completion"].get("status", {}).get("status") != expected_status
            ):
                raise ValueError("SAM3D terminal ownership evidence missing/inconsistent")
            if job.state == "succeeded":
                for descriptor in json.loads(job.result_json or b"{}")["outputs"]:
                    owner.get_blob(descriptor["blob_digest"])
            return job
        receipt = record["receipt"]
        if record["completion"] is None:
            lookup = self.client.lookup(
                request.submission_key,
                request_digest=intent["request_digest"],
                backend_digest=self.backend_digest,
                expected_job_id=receipt["job_id"] if receipt else None,
            )
            if lookup.state != "registered" or lookup.receipt is None:
                raise Sam3DUnknown("SAM3D original request missing/expired; no resubmission")
            receipt = to_primitive(lookup.receipt)
            owner.fix_evidence(request, "receipt", receipt)
            status = self.client.query(receipt["job_id"])
            if status.get("id") != receipt["job_id"]:
                raise Sam3DUnknown("SAM3D status job identity mismatch")
            if status["status"] == "failed":
                # Failure must also be tied to the original identity, not just a string job ID.
                if (
                    status.get("request_digest") != intent["request_digest"]
                    or status.get("deployment") != self.deployment
                    or status.get("submission_key") != request.submission_key
                ):
                    raise Sam3DUnknown("SAM3D failure identity mismatch")
                owner.fix_evidence(request, "completion", {"status": status})
            elif status["status"] == "completed":
                if not isinstance(status.get("file_descriptors"), dict):
                    raise Sam3DUnknown("SAM3D result descriptors missing")
                # Fix descriptors BEFORE download. Recovery cannot accept changed terminal files.
                owner.fix_evidence(request, "completion", {"status": status})
            else:
                return job
        completion = owner.bridge_record(request)["completion"]
        status = completion["status"]
        if status["status"] == "failed":
            return owner.transition(
                request,
                expected="running",
                state="failed",
                error={
                    "code": "SAM3D_FAILED",
                    "detail": "SAM3D reported failure; upstream evidence retained",
                },
            )
        # Downloads are allowed until an output import is durably fixed, but never repair
        # a previously committed blob. Each file gets a deterministic digest from terminal evidence.
        files = {}
        descriptors = status["file_descriptors"]
        for name in REQUIRED_FILES:
            descriptor = descriptors.get(name)
            if not isinstance(descriptor, dict) or set(descriptor) != {"sha256", "byte_length"}:
                raise Sam3DUnknown("invalid SAM3D file descriptor")
            data = self.client.download(receipt["job_id"], name)
            if descriptor != {"sha256": sha256_bytes(data), "byte_length": len(data)}:
                raise Sam3DUnknown("SAM3D download changed from fixed evidence")
            files[name] = data
        outputs = verify_result(
            artifacts,
            files=files,
            status=status,
            intent=intent,
            original_mask=owner.get_blob(intent["request_identity"]["mask"]),
        )
        return owner.publish_outputs(request, outputs)

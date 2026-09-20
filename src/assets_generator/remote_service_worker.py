"""Explicit trusted service execution; HTTP submission itself never runs model code."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .errors import PipelineError, ServiceExecutionUncertain
from .remote_protocol import RemoteJob, RemoteOutput, RemoteRequest
from .remote_service_store import RemoteServiceStore
from .serialization import sha256_bytes


@dataclass(frozen=True)
class ServiceOutput:
    data: bytes
    media_type: str


ServiceHandler = Callable[[RemoteRequest, RemoteServiceStore], Mapping[str, ServiceOutput]]


def execute_service_job(
    store: RemoteServiceStore, request: RemoteRequest, handler: ServiceHandler
) -> RemoteJob:
    """Claim once, execute synchronously and publish verified outputs.

    A process interruption leaves running intact. There is no automatic retry,
    subprocess launch or remote cancellation in this CPU/service worker primitive.
    Storage failures propagate without converting an uncertain commit into failure.
    """
    store.transition(request, expected="queued", state="running", require_idle=True)
    return _execute_claimed_job(store, request, handler)


def execute_next_service_job(
    store: RemoteServiceStore, handler: ServiceHandler
) -> RemoteJob | None:
    request = store.claim_next_queued()
    if request is None:
        return None
    return _execute_claimed_job(store, request, handler)


def _execute_claimed_job(
    store: RemoteServiceStore, request: RemoteRequest, handler: ServiceHandler
) -> RemoteJob:
    try:
        outputs = dict(handler(request, store))
        descriptors = []
        total = 0
        for key, output in outputs.items():
            if not isinstance(output, ServiceOutput) or not isinstance(output.data, bytes):
                raise ValueError("handler outputs require ServiceOutput bytes")
            total += len(output.data)
            if total > 128 * 1024 * 1024:
                raise ValueError("service outputs exceed aggregate byte limit")
            descriptors.append(
                RemoteOutput(key, sha256_bytes(output.data), len(output.data), output.media_type)
            )
    except ServiceExecutionUncertain:
        # A remote process may still be running. Keep its claim across restart;
        # terminal failure would incorrectly release serial queue admission.
        raise
    except Exception as error:
        return store.transition(
            request,
            expected="running",
            state="failed",
            error={
                "code": error.code.value
                if isinstance(error, PipelineError)
                else "SERVICE_HANDLER_FAILED",
                "detail": str(error) or type(error).__name__,
            },
        )
    for descriptor in descriptors:
        store.put_blob(outputs[descriptor.output_id].data, descriptor.blob_digest)
    return store.transition(
        request,
        expected="running",
        state="succeeded",
        result={
            "outputs": [
                {
                    "output_id": item.output_id,
                    "blob_digest": item.blob_digest,
                    "byte_length": item.byte_length,
                    "media_type": item.media_type,
                }
                for item in descriptors
            ]
        },
    )

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    INPUT_ERROR = "input_error"
    CONTRACT_ERROR = "contract_error"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    BACKEND_TIMEOUT = "backend_timeout"
    BACKEND_FAILED = "backend_failed"
    OUTPUT_INVALID = "output_invalid"
    RELEASE_FAILED = "release_failed"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


class PipelineError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ServiceExecutionUncertain(ValueError):
    """Execution may still exist; preserve the durable claim and do not retry.

    Service handlers raise this instead of reporting a terminal failure when
    remote acknowledgement or execution evidence is unavailable.
    """


def classify_error(error: Exception) -> ErrorCode:
    from .contracts import ContractError

    if isinstance(error, PipelineError):
        return error.code
    if isinstance(error, ContractError):
        return ErrorCode.CONTRACT_ERROR
    if isinstance(error, (FileNotFoundError, IsADirectoryError, PermissionError)):
        return ErrorCode.INPUT_ERROR
    return ErrorCode.INTERNAL_ERROR

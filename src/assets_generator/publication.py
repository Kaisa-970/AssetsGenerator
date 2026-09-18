"""Atomic no-replace directory publication on the supported Linux filesystem."""

from __future__ import annotations

import os
from ctypes import CDLL, c_char_p, c_int, get_errno
from errno import ENOSYS
from pathlib import Path


def publish_staged_release(staging_path: Path, output_path: Path) -> None:
    libc = CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(ENOSYS, "atomic no-replace publication is unavailable")
    renameat2.argtypes = [c_int, c_char_p, c_int, c_char_p, c_int]
    renameat2.restype = c_int
    if renameat2(-100, os.fsencode(staging_path), -100, os.fsencode(output_path), 1) != 0:
        error_number = get_errno()
        raise OSError(error_number, os.strerror(error_number), output_path)

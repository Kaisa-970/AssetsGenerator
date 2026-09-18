"""Lightweight launcher executable. Do not import model/backend modules here."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

# Direct-file execution works even with a Backend-specific cwd/PYTHONPATH.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).parent.parent))

from assets_generator.workbench_models import record_payload
from assets_generator.workbench_process import LinuxProcessProbe


def main() -> int:
    descriptor = int(sys.argv[1])
    command = json.loads(sys.argv[2])
    with socket.socket(fileno=descriptor) as control:
        identity = LinuxProcessProbe().identify(os.getpid())
        if identity.pgid != identity.pid:
            raise RuntimeError("launcher does not own its process group")
        control.sendall(json.dumps(record_payload(identity)).encode() + b"\n")
        authorization = bytearray()
        while b"\n" not in authorization:
            chunk = control.recv(32)
            if not chunk:
                return 125
            authorization.extend(chunk)
            if len(authorization) > 32:
                return 125
        if bytes(authorization) != b"RELEASE\n":
            return 125
    # A direct child inherits this group. Do not create a new session for it.
    task = subprocess.Popen(command, stdin=subprocess.DEVNULL, close_fds=True)
    code = task.wait()
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    raise SystemExit(main())

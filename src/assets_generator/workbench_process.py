"""Linux process evidence and a one-shot gated Backend launch channel.

No method grants durable authorization: the caller must commit release_authorized
before calling release(). Closing a channel never terminates an authorized task.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import socket
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from .workbench_models import ProcessIdentity, ProcessObservation, decode_record


@dataclass(frozen=True)
class _ProcessStat:
    pid: int
    state: str
    pgid: int
    starttime_ticks: int


def _read_stat(path: Path) -> _ProcessStat:
    text = path.read_text(encoding="utf-8")
    prefix, separator, remainder = text.rpartition(")")
    if not separator:
        raise ValueError("invalid Linux process stat")
    pid = int(prefix.split("(", 1)[0].strip())
    fields = remainder.split()
    result = _ProcessStat(pid, fields[0], int(fields[2]), int(fields[19]))
    if result.pid <= 0 or result.pgid < 0 or result.starttime_ticks < 0:
        raise ValueError("incomplete Linux process stat")
    return result


class LinuxProcessProbe:
    def __init__(
        self, *, proc_root: Path = Path("/proc"), machine_id_path: Path = Path("/etc/machine-id")
    ) -> None:
        self.proc_root = proc_root
        self.machine_id_path = machine_id_path

    def host_boot(self) -> tuple[str, str]:
        machine = self.machine_id_path.read_text(encoding="ascii").strip()
        boot = (self.proc_root / "sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        # Namespace changes cannot be interpreted as the disappearance of old PIDs.
        namespace = os.readlink(self.proc_root / "self/ns/pid")
        if not machine or not boot or not namespace:
            raise ValueError("missing host/boot/namespace evidence")
        host = "sha256:" + hashlib.sha256((machine + "\0" + namespace).encode()).hexdigest()
        return host, boot

    def identify(self, pid: int) -> ProcessIdentity:
        host, boot = self.host_boot()
        stat = _read_stat(self.proc_root / str(pid) / "stat")
        if stat.pid != pid:
            raise ValueError("process stat PID mismatch")
        return ProcessIdentity(host, boot, stat.pid, stat.starttime_ticks, stat.pgid)

    def observe(self, identity: ProcessIdentity) -> ProcessObservation:
        observed_at = datetime.now(timezone.utc).isoformat()
        observed = None
        members: list[ProcessIdentity] = []
        try:
            host, boot = self.host_boot()
            if host != identity.host_id:
                return ProcessObservation(
                    observed_at, "unknown", reason="host or PID namespace differs"
                )
            if boot != identity.boot_id:
                return ProcessObservation(observed_at, "exited", reason="same host has rebooted")
            try:
                stat = _read_stat(self.proc_root / str(identity.pid) / "stat")
            except FileNotFoundError:
                stat = None
            if stat is not None:
                observed = ProcessIdentity(host, boot, stat.pid, stat.starttime_ticks, stat.pgid)
            for path in self.proc_root.iterdir():
                if not path.name.isdecimal():
                    continue
                try:
                    member = _read_stat(path / "stat")
                except FileNotFoundError:
                    continue
                if member.pid != int(path.name):
                    raise ValueError("process stat PID mismatch")
                if member.pgid == identity.pgid and member.state not in {"Z", "X", "x"}:
                    members.append(
                        ProcessIdentity(host, boot, member.pid, member.starttime_ticks, member.pgid)
                    )
            members.sort(key=lambda item: item.pid)
            if observed is not None and observed != identity:
                return ProcessObservation(
                    observed_at,
                    "unknown",
                    observed,
                    tuple(members),
                    "PID reused or process group identity changed",
                )
            if members or (stat is not None and stat.state not in {"Z", "X", "x"}):
                return ProcessObservation(
                    observed_at,
                    "alive",
                    observed,
                    tuple(members),
                    "registered process or group remains active",
                )
            # Recheck leader after enumeration so a disappearing/reused PID cannot be
            # mistaken for an empty registered group based on one stale read.
            try:
                latest = self.identify(identity.pid)
            except FileNotFoundError:
                latest = None
            if latest is not None and latest != identity:
                return ProcessObservation(
                    observed_at, "unknown", latest, reason="PID changed during probe"
                )
            return ProcessObservation(
                observed_at,
                "exited",
                observed,
                reason="registered process exited and group is empty",
            )
        except (OSError, ValueError, IndexError) as error:
            return ProcessObservation(
                observed_at,
                "unknown",
                observed,
                tuple(members),
                f"cannot verify process evidence: {error}",
            )


class GatedLaunchChannel:
    """Launch a lightweight new-session launcher; no backend starts before release.

    identify() must succeed before release(). A failed/closed channel cannot be
    reused. wait() reports launcher/backend exit code, not stage completion.
    stdout/stderr default to DEVNULL; use file handles to avoid pipe deadlocks.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        stdout: IO[Any] | int | None = None,
        stderr: IO[Any] | int | None = None,
        handshake_timeout: float = 10.0,
    ) -> None:
        if not command or any(
            not isinstance(part, str) or not part or "\0" in part for part in command
        ):
            raise ValueError("backend command must be a nonempty argv sequence")
        if handshake_timeout <= 0:
            raise ValueError("handshake timeout must be positive")
        parent, child = socket.socketpair()
        self._control: socket.socket | None = parent
        self._identity: ProcessIdentity | None = None
        self._released = False
        self._timeout = handshake_timeout
        launcher = Path(__file__).with_name("workbench_launcher.py")
        try:
            self._process = subprocess.Popen(
                [sys.executable, str(launcher), str(child.fileno()), json.dumps(list(command))],
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL if stdout is None else stdout,
                stderr=subprocess.DEVNULL if stderr is None else stderr,
                pass_fds=(child.fileno(),),
                start_new_session=True,
            )
        except BaseException:
            parent.close()
            self._control = None
            raise
        finally:
            child.close()

    @property
    def pid(self) -> int:
        return self._process.pid

    def identify(self) -> ProcessIdentity:
        if self._identity is not None:
            return self._identity
        if self._control is None:
            raise RuntimeError("launcher control channel is closed")
        deadline = time.monotonic() + self._timeout
        data = bytearray()
        try:
            while b"\n" not in data:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([self._control], [], [], remaining)[0]:
                    raise TimeoutError("launcher identity handshake timed out")
                chunk = self._control.recv(4096)
                if not chunk:
                    raise RuntimeError("launcher exited before identity handshake")
                data.extend(chunk)
                if len(data) > 8192:
                    raise ValueError("oversized launcher identity")
            identity = decode_record(ProcessIdentity, json.loads(data))
            if identity.pid != self.pid or identity.pgid != self.pid:
                raise ValueError("launcher must own its independent process group")
            if LinuxProcessProbe().identify(self.pid) != identity:
                raise ValueError("launcher identity does not match Linux process evidence")
            self._identity = identity
            return identity
        except BaseException:
            self.close()
            raise

    def release(self) -> None:
        if self._released or self._identity is None or self._control is None:
            raise RuntimeError("launcher requires identification and exactly one authorization")
        self._released = True  # A failed send is never safe to retry.
        try:
            self._control.sendall(b"RELEASE\n")
        finally:
            self.close()

    def close(self) -> None:
        if self._control is not None:
            self._control.close()
            self._control = None

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def __enter__(self) -> GatedLaunchChannel:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from assets_generator.workbench_models import ProcessIdentity
from assets_generator.workbench_process import GatedLaunchChannel, LinuxProcessProbe, _read_stat

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process semantics")


def test_gated_launcher_requires_authorization_and_waits(tmp_path):
    target = tmp_path / "backend"
    with (tmp_path / "stdout").open("wb") as log:
        channel = GatedLaunchChannel(
            [
                sys.executable,
                "-c",
                "import os,time,pathlib; print(os.getcwd(),flush=True); "
                "pathlib.Path('backend').write_text(os.environ['TOKEN']); time.sleep(.15)",
            ],
            cwd=tmp_path,
            env={**os.environ, "TOKEN": "explicit"},
            stdout=log,
        )
        identity = channel.identify()
        assert identity.pid == identity.pgid == channel.pid
        assert LinuxProcessProbe().observe(identity).result == "alive"
        assert not target.exists()
        with pytest.raises(subprocess.TimeoutExpired):
            channel.wait(0.02)
        channel.release()
        with pytest.raises(RuntimeError):
            channel.release()
        assert channel.wait(5) == 0
    assert target.read_text() == "explicit"
    assert (tmp_path / "stdout").read_text().strip() == str(tmp_path)
    assert LinuxProcessProbe().observe(identity).result == "exited"


def test_close_before_authorization_never_runs_backend(tmp_path):
    target = tmp_path / "not-started"
    channel = GatedLaunchChannel([sys.executable, "-c", f"open({str(target)!r},'w').close()"])
    channel.identify()
    channel.close()
    assert channel.wait(5) == 125
    assert not target.exists()


def test_release_without_identify_is_rejected(tmp_path):
    channel = GatedLaunchChannel([sys.executable, "-c", "raise SystemExit(9)"])
    try:
        with pytest.raises(RuntimeError):
            channel.release()
    finally:
        channel.close()
        channel.wait(5)


def test_authorized_backend_exit_status():
    channel = GatedLaunchChannel([sys.executable, "-c", "raise SystemExit(7)"])
    channel.identify()
    channel.release()
    assert channel.wait(5) == 7


def test_group_residue_remains_alive_after_launcher_exit(tmp_path):
    marker = tmp_path / "child"
    script = (
        "import subprocess,sys,pathlib; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(1)']); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))"
    )
    channel = GatedLaunchChannel([sys.executable, "-c", script, str(marker)])
    identity = channel.identify()
    channel.release()
    assert channel.wait(5) == 0
    probe = LinuxProcessProbe()
    observation = probe.observe(identity)
    assert observation.result == "alive"
    assert int(marker.read_text()) in {item.pid for item in observation.group_members}
    deadline = time.monotonic() + 5
    while probe.observe(identity).result != "exited" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert probe.observe(identity).result == "exited"


def _fake_probe(tmp_path):
    proc = tmp_path / "proc"
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("boot")
    (proc / "self/ns").mkdir(parents=True)
    (proc / "self/ns/pid").symlink_to("pid:[123]")
    machine = tmp_path / "machine-id"
    machine.write_text("host")
    probe = LinuxProcessProbe(proc_root=proc, machine_id_path=machine)
    host, boot = probe.host_boot()
    return probe, ProcessIdentity(host, boot, 123, 777, 123)


def _stat(root: Path, pid=123, start=777, group=123, state="S"):
    directory = root / str(pid)
    directory.mkdir(exist_ok=True)
    fields = [state, "1", str(group)] + ["0"] * 16 + [str(start)]
    (directory / "stat").write_text(f"{pid} (name with ) chars) " + " ".join(fields))


def test_probe_pid_reuse_is_unknown_even_if_group_empty(tmp_path):
    probe, identity = _fake_probe(tmp_path)
    _stat(probe.proc_root, start=999, group=999)
    observation = probe.observe(identity)
    assert observation.result == "unknown"
    assert observation.observed_identity.starttime_ticks == 999


def test_probe_other_host_unknown_and_reboot_exited(tmp_path):
    probe, identity = _fake_probe(tmp_path)
    assert probe.observe(dataclasses.replace(identity, host_id="other")).result == "unknown"
    assert probe.observe(dataclasses.replace(identity, boot_id="old")).result == "exited"


def test_probe_permission_error_unknown(tmp_path, monkeypatch):
    probe, identity = _fake_probe(tmp_path)
    _stat(probe.proc_root)
    original = Path.read_text

    def denied(path, *args, **kwargs):
        if path.name == "stat":
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    assert probe.observe(identity).result == "unknown"


def test_probe_group_member_without_leader_blocks_retry(tmp_path):
    probe, identity = _fake_probe(tmp_path)
    _stat(probe.proc_root, pid=124, start=888)
    observation = probe.observe(identity)
    assert observation.result == "alive"
    assert observation.group_members[0].pid == 124


def test_proc_stat_handles_spaces_and_parentheses(tmp_path):
    _stat(tmp_path)
    stat = _read_stat(tmp_path / "123/stat")
    assert (stat.pid, stat.starttime_ticks, stat.pgid) == (123, 777, 123)


def test_group_scan_permission_failure_blocks_exit(tmp_path, monkeypatch):
    probe, identity = _fake_probe(tmp_path)
    _stat(probe.proc_root, pid=888, group=999)
    original = Path.read_text

    def denied(path, *args, **kwargs):
        if path.parent.name == "888":
            raise PermissionError("group member unreadable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    assert probe.observe(identity).result == "unknown"


def test_zombie_is_exited_only_with_empty_live_group(tmp_path):
    probe, identity = _fake_probe(tmp_path)
    _stat(probe.proc_root, state="Z")
    assert probe.observe(identity).result == "exited"
    _stat(probe.proc_root, pid=124, start=778)
    assert probe.observe(identity).result == "alive"


def test_launcher_control_not_inherited_by_backend(tmp_path):
    target = tmp_path / "fds"
    channel = GatedLaunchChannel(
        [
            sys.executable,
            "-c",
            "import pathlib,os,sys; pathlib.Path(sys.argv[1]).write_text(str("
            "[os.readlink('/proc/self/fd/'+fd) for fd in os.listdir('/proc/self/fd') "
            "if fd.isdigit() and int(fd)>2 and os.path.exists('/proc/self/fd/'+fd)]))",
            str(target),
        ]
    )
    channel.identify()
    channel.release()
    assert channel.wait(5) == 0
    assert target.read_text() == "[]"


@pytest.mark.parametrize("authorized", [False, True])
def test_real_parent_disappearance_obeys_gate(tmp_path, authorized):
    import json

    from assets_generator.workbench_models import decode_record

    identity_path = tmp_path / "identity.json"
    target = tmp_path / "backend-started"
    backend = (
        "import pathlib,sys,time; time.sleep(.15); "
        "pathlib.Path(sys.argv[1]).write_text('executed'); time.sleep(.2)"
    )
    parent = (
        "import json,os,sys; from pathlib import Path; "
        "from assets_generator.workbench_process import GatedLaunchChannel; "
        "from assets_generator.workbench_models import record_payload; "
        "channel=GatedLaunchChannel([sys.executable,'-c',sys.argv[3],sys.argv[2]]); "
        "identity=channel.identify(); "
        "Path(sys.argv[1]).write_text(json.dumps(record_payload(identity))); "
        + ("channel.release(); " if authorized else "")
        + "os._exit(0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", parent, str(identity_path), str(target), backend],
        check=True,
        timeout=5,
    )
    assert result.returncode == 0
    identity = decode_record(ProcessIdentity, json.loads(identity_path.read_text()))
    probe = LinuxProcessProbe()
    deadline = time.monotonic() + 5
    while probe.observe(identity).result != "exited" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert probe.observe(identity).result == "exited"
    assert target.exists() == authorized

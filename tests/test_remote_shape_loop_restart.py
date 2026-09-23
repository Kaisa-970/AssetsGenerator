"""Real process interruption of the opt-in loop; no GPU/model quality claims."""

import os
import subprocess
import sys
import time

import pytest
from test_remote_http import request

from assets_generator.remote_protocol import RemoteRequest
from assets_generator.remote_service_store import RemoteServiceStore

SCRIPT = """
import sys,threading,time
from pathlib import Path
from assets_generator.remote_protocol import RemoteIdentity
from assets_generator.remote_service_store import RemoteServiceStore
from assets_generator.remote_service_worker import ServiceOutput
from assets_generator.remote_shape_loop import run_loop
root=Path(sys.argv[1])
store=RemoteServiceStore(root/'service.sqlite',RemoteIdentity(sys.argv[2],sys.argv[3]))
def handler(req, owner):
    with (root/'calls').open('a') as stream:
        stream.write(req.submission_key+'\\n')
        stream.flush()
    if sys.argv[4]=='block':
        threading.Event().wait()
    return {'result':ServiceOutput(b'fixture','application/octet-stream')}
try:
    run_loop(store,handler,stop=threading.Event(),interval=.05)
finally:
    store.close()
"""


def launch(root, identity, mode):
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            SCRIPT,
            str(root),
            identity.service_id,
            identity.backend_digest,
            mode,
        ],
        env={**os.environ},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def until(predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    pytest.fail("worker state did not arrive")


@pytest.mark.parametrize("completed", [False, True])
def test_hard_kill_and_new_loop_preserve_claims(tmp_path, completed):
    identity = request().identity
    first = RemoteRequest.create(identity, "first", {})
    second = RemoteRequest.create(identity, "second", {})
    store = RemoteServiceStore(tmp_path / "service.sqlite", identity)
    store.submit(first)
    process = launch(tmp_path, identity, "finish" if completed else "block")
    restarted = None
    try:
        until(lambda: (tmp_path / "calls").exists())
        if completed:
            until(lambda: store.lookup(first).state == "succeeded")
        process.kill()
        process.communicate(timeout=10)
        store.submit(second)
        restarted = launch(tmp_path, identity, "finish")
        if completed:
            until(lambda: store.lookup(second).state == "succeeded")
            assert (tmp_path / "calls").read_text().splitlines() == ["first", "second"]
        else:
            _, stderr = restarted.communicate(timeout=10)
            assert restarted.returncode != 0
            assert b"running" in stderr
            assert store.lookup(first).state == "running"
            assert store.lookup(second).state == "queued"
            assert (tmp_path / "calls").read_text().splitlines() == ["first"]
    finally:
        for worker in (process, restarted):
            if worker is not None:
                if worker.poll() is None:
                    worker.kill()
                worker.communicate(timeout=10)
        store.close()

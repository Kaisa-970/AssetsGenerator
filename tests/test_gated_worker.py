import subprocess
import sys

import pytest

from assets_generator.errors import ErrorCode, PipelineError
from assets_generator.gated_worker import GatedWorkerCallbacks, run_gated_process
from assets_generator.workbench_models import ProcessIdentity, ProcessObservation
from assets_generator.worker import ProcessJobRequest


def callbacks(events, *, fail=None):
    def record(phase, *values):
        events.append((phase, values))
        if phase == fail:
            raise OSError(f"{phase} commit failed")

    return GatedWorkerCallbacks(
        lambda *args: record("prepared", *args),
        lambda *args: record("identified", *args),
        lambda *args: record("authorized", *args),
        lambda *args: record("exited", *args),
    )


@pytest.mark.parametrize("phase", ["prepared", "identified", "authorized"])
def test_callback_commit_failure_never_releases_backend(tmp_path, phase):
    events = []
    channels = []

    class Channel:
        def __init__(self, *args, **kwargs):
            channels.append(self)
            self.closed = False

        def identify(self):
            return ProcessIdentity("host", "boot", 123, 456, 123)

        def release(self):
            pytest.fail("backend released before all durable callbacks succeeded")

        def close(self):
            self.closed = True

    with pytest.raises(OSError, match=f"{phase} commit failed"):
        run_gated_process(
            ProcessJobRequest(["unused"], tmp_path, 1, "key"),
            callbacks(events, fail=phase),
            channel_factory=Channel,
        )
    assert all(channel.closed for channel in channels)
    assert bool(channels) == (phase != "prepared")


def test_real_nonzero_preserves_exit_evidence_and_error_code(tmp_path):
    events = []
    with pytest.raises(PipelineError) as error:
        run_gated_process(
            ProcessJobRequest([sys.executable, "-c", "import sys; sys.exit(7)"], tmp_path, 10, "k"),
            callbacks(events),
        )
    assert error.value.code == ErrorCode.BACKEND_FAILED
    assert [phase for phase, _ in events] == ["prepared", "identified", "authorized", "exited"]
    observation, code = events[-1][1]
    assert observation.result == "exited"
    assert code == 7


@pytest.mark.parametrize("uncertain", [False, True])
def test_timeout_only_kills_verified_identity_and_preserves_code(tmp_path, monkeypatch, uncertain):
    events = []
    killed = []
    identity = ProcessIdentity("host", "boot", 123, 456, 123)

    class Channel:
        def __init__(self, *args, **kwargs):
            self.waits = 0

        def identify(self):
            return identity

        def release(self):
            assert events[-1][0] == "authorized"

        def wait(self, timeout):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("fake", timeout)
            return -9

        def close(self):
            pass

    class Probe:
        calls = 0

        def observe(self, original):
            assert original == identity
            self.calls += 1
            if uncertain:
                return ProcessObservation("now", "unknown")
            return ProcessObservation("now", "alive" if self.calls == 1 else "exited", identity)

    monkeypatch.setattr(
        "assets_generator.gated_worker.os.killpg", lambda *args: killed.append(args)
    )
    with pytest.raises(PipelineError) as error:
        run_gated_process(
            ProcessJobRequest(["unused"], tmp_path, 1, "key"),
            callbacks(events),
            channel_factory=Channel,
            probe=Probe(),
        )
    assert error.value.code == ErrorCode.BACKEND_TIMEOUT
    assert bool(killed) is not uncertain
    assert any(phase == "exited" for phase, _ in events) is not uncertain

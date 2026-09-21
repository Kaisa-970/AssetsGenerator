"""Loopback unified service for a pinned local SAM3 deployment."""

import argparse
import json
import signal
from pathlib import Path
from threading import Event
from typing import Any

from .remote_protocol import RemoteIdentity, RemoteJob, RemoteRequest
from .remote_service_http import create_remote_server
from .remote_service_store import RemoteServiceStore
from .remote_service_worker import ServiceHandler, ServiceOutput, execute_next_service_job
from .sam3_text_service import Sam3TextHandler
from .serialization import canonical_json_bytes


def execute_verified_job(
    owner: RemoteServiceStore, handler: ServiceHandler, profile: dict[str, Any]
) -> RemoteJob | None:
    from .sam3_text_identity import deployment_identity

    def verified_handler(
        request: RemoteRequest, service: RemoteServiceStore
    ) -> dict[str, ServiceOutput]:
        if deployment_identity(profile) != owner.identity:
            raise ValueError("SAM3 deployment changed")
        return dict(handler(request, service))

    return execute_next_service_job(owner, verified_handler)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("init", "serve", "work"))
    p.add_argument("--profile", type=Path, required=True)
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--port", type=int, default=8773)
    a = p.parse_args()
    raw = json.loads(a.profile.read_bytes())
    from .sam3_text_identity import deployment_identity

    identity = deployment_identity(raw)
    expected = RemoteIdentity(raw["service_id"], raw["backend_digest"])
    if identity != expected:
        raise ValueError("SAM3 deployment differs from pinned profile")
    db = a.directory / "service.sqlite"
    if a.action == "init":
        a.directory.mkdir(parents=True, exist_ok=False)
    elif not db.is_file():
        raise ValueError("SAM3 service database missing; refusing to recreate")
    owner = RemoteServiceStore(db, identity)
    try:
        if a.action == "init":
            print(canonical_json_bytes(identity).decode())
        elif a.action == "serve":
            server = create_remote_server(owner, port=a.port)
            try:
                print(f"http://127.0.0.1:{server.server_port}", flush=True)
                server.serve_forever()
            finally:
                server.server_close()
        else:
            import fcntl

            with (a.directory / "worker.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                stop = Event()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    signal.signal(sig, lambda *_: stop.set())
                handler = Sam3TextHandler(
                    identity,
                    Path(raw["python"]),
                    Path(raw["repo"]),
                    Path(raw["checkpoint"]),
                    Path(raw["runner"]),
                    a.directory / "work",
                )

                while not stop.is_set():
                    # Unknown running jobs block claim_next_queued; never launch again.
                    job = execute_verified_job(owner, handler, raw)
                    if job:
                        print(json.dumps({"job_id": job.job_id, "state": job.state}), flush=True)
                    stop.wait(3)
    finally:
        owner.close()


if __name__ == "__main__":
    main()

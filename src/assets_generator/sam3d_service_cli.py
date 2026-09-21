"""Explicit SAM3D compatibility service; starting the listener never starts inference."""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
from pathlib import Path
from threading import Event

from .artifact_store import LocalArtifactStore
from .remote_protocol import decode_remote_json
from .remote_service_http import create_remote_server
from .sam3d_bridge import Sam3DBridge
from .sam3d_http import Sam3DClient, Sam3DUnknown
from .sam3d_service_store import Sam3DServiceStore
from .serialization import to_primitive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("identity", "init", "serve", "list", "execute-next", "recover", "work")
    )
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--service-id", default="sam3d-bridge")
    parser.add_argument("--key-env", default="SAM3D_API_KEY")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--key")
    parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--poll-interval", type=float, default=5)
    args = parser.parse_args()
    if not math.isfinite(args.poll_interval) or not 0.1 <= args.poll_interval <= 60:
        parser.error("poll interval must be finite and in 0.1..60")
    deployment = decode_remote_json(args.deployment.read_bytes())
    if not isinstance(deployment, dict):
        parser.error("deployment must be the reviewed capabilities.deployment object")
    # Offline actions never use this placeholder to make an HTTP request.
    secret = os.environ.get(args.key_env)
    if args.action in {"execute-next", "recover", "work"} and not secret:
        parser.error("API key environment variable is absent")
    bridge = Sam3DBridge(Sam3DClient(args.endpoint, secret or "offline-unused"), deployment)
    identity = bridge.identity(args.service_id)
    if args.action == "identity":
        print(json.dumps(to_primitive(identity), indent=2))
        return 0
    if args.directory is None:
        parser.error("--directory required")
    if args.action == "recover" and not args.key:
        parser.error("recover requires original --key")
    if not 0 <= args.port <= 65535:
        parser.error("port must be in 0..65535")
    path = args.directory / "service.sqlite"
    if args.action == "init":
        args.directory.mkdir(parents=True, exist_ok=False)
    elif not path.is_file():
        parser.error("original service database missing; refusing to recreate")
    owner = Sam3DServiceStore(path, identity)
    try:
        if args.action == "init":
            owner.initialize_bridge()
            print(json.dumps(to_primitive(identity), indent=2))
            return 0
        # No schema repair in serve/execute/recover.
        owner.db.execute("SELECT key,intent,receipt,completion FROM sam3d_jobs LIMIT 0")
        if args.action == "serve":
            server = create_remote_server(owner, port=args.port)
            print(f"http://127.0.0.1:{server.server_port}/ (Ctrl+C to close)", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        if args.action == "list":
            print(json.dumps(owner.list_jobs(), indent=2))
            return 0
        # Scratch conversion only. Authoritative output bytes live atomically in SQLite.
        artifacts = LocalArtifactStore(args.directory / "staging")
        if args.action == "work":
            # One cooperating CLI loop per database. The SQLite claim remains the
            # authority even if an explicit execute-next races with this worker.
            import fcntl

            from .sam3d_service_loop import run_loop

            with (args.directory / "worker.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError("SAM3D worker already running for this directory") from None
                stop = Event()
                previous = {
                    sig: signal.signal(sig, lambda *_: stop.set())
                    for sig in (signal.SIGINT, signal.SIGTERM)
                }
                try:
                    run_loop(
                        owner,
                        bridge,
                        artifacts,
                        stop=stop,
                        interval=args.poll_interval,
                        report=lambda value: print(value, flush=True),
                    )
                finally:
                    for sig, handler in previous.items():
                        signal.signal(sig, handler)
            return 0
        if args.action == "execute-next":
            job = bridge.start_next(owner, artifacts)
        else:
            request = owner.request_for(args.key)
            if request is None:
                raise ValueError("original job missing")
            job = bridge.recover(owner, request, artifacts)
        value = (
            None
            if job is None
            else {
                "job_id": job.job_id,
                "state": job.state,
                "result": json.loads(job.result_json) if job.result_json else None,
                "error": json.loads(job.error_json) if job.error_json else None,
            }
        )
        print(json.dumps(value, indent=2))
        return 1 if job is not None and job.state == "failed" else 0
    except Sam3DUnknown:
        print(
            json.dumps(
                {"state": "uncertain", "next_action": "recover original key; never resubmit"}
            )
        )
        return 3
    finally:
        owner.close()


if __name__ == "__main__":
    raise SystemExit(main())

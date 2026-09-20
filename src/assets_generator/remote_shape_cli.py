"""Explicit local shape service commands; no background queue or implicit inference."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .remote_service_http import create_remote_server
from .remote_service_store import RemoteServiceStore
from .remote_service_worker import execute_next_service_job, execute_service_job
from .remote_shape_profile import shape_handler_from_profile
from .serialization import to_primitive
from .workbench_profiles import load_shape_profiles


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "action", choices=("serve", "execute", "observe", "inspect", "drain", "list")
    )
    result.add_argument("--config", type=Path, required=True)
    result.add_argument("--profile", required=True)
    result.add_argument("--service-id", required=True)
    result.add_argument("--database", type=Path, required=True)
    result.add_argument("--workspace", type=Path, required=True)
    result.add_argument("--job")
    result.add_argument("--max-jobs", type=int, default=1)
    result.add_argument("--limit", type=int, default=100)
    result.add_argument("--before", type=int)
    result.add_argument("--port", type=int, default=8770)
    return result


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    if args.action not in {"serve", "drain", "list"} and not args.job:
        cli.error("--job is required for execute, observe and inspect")
    if args.max_jobs < 1 or args.max_jobs > 1000:
        cli.error("--max-jobs must be in 1..1000")
    if not 1 <= args.limit <= 1000 or (args.before is not None and args.before < 1):
        cli.error("--limit must be in 1..1000 and --before must be positive")
    if not 0 <= args.port <= 65535:
        cli.error("--port must be in 0..65535")
    store = None
    try:
        profiles = load_shape_profiles(
            json.loads(args.config.read_bytes()),
            progress=lambda message: print(message, file=sys.stderr),
        )
        if args.profile not in profiles:
            raise ValueError("unknown shape profile")
        handler = shape_handler_from_profile(
            profiles[args.profile], service_id=args.service_id, workspace=args.workspace
        )
        store = RemoteServiceStore(args.database, handler.identity)
        if args.action == "list":
            print(json.dumps(store.list_jobs(limit=args.limit, before=args.before)))
            return 0
        if args.action == "drain":
            for _ in range(args.max_jobs):
                job = execute_next_service_job(store, handler)
                if job is None:
                    break
                print(json.dumps({"job_id": job.job_id, "state": job.state}), flush=True)
                if job.state != "succeeded":
                    return 1
            return 0
        if args.action == "serve":
            server = create_remote_server(store, port=args.port)
            try:
                print(
                    json.dumps(
                        {
                            "endpoint": f"http://127.0.0.1:{server.server_port}",
                            **to_primitive(handler.identity),
                        }
                    ),
                    flush=True,
                )
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        else:
            request = store.request_for(args.job)
            if request is None:
                raise ValueError("job not found")
            if args.action == "execute":
                job = execute_service_job(store, request, handler)
                print(
                    json.dumps(
                        {
                            "job_id": job.job_id,
                            "state": job.state,
                            "error": json.loads(job.error_json) if job.error_json else None,
                        }
                    )
                )
                return 0 if job.state == "succeeded" else 1
            if args.action == "observe":
                print(json.dumps(to_primitive(store.observe_worker(request))))
            else:
                found = store.lookup(request)
                assert found is not None
                job = found
                print(
                    json.dumps(
                        {
                            "job_id": job.job_id,
                            "state": job.state,
                            "result": json.loads(job.result_json) if job.result_json else None,
                            "error": json.loads(job.error_json) if job.error_json else None,
                        }
                    )
                )
        return 0
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"shape service: {error}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""Local protocol listener and explicit executor commands for ComfyUI image jobs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .artifact_store import LocalArtifactStore
from .comfy_cli import _job_value
from .comfy_executor import execute_next_image, recover_image_job
from .comfy_profile import ComfyImageProfile
from .comfy_submission import ComfySubmissionUnknown
from .remote_service_http import create_remote_server
from .remote_service_store import RemoteServiceStore


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("comfy-service", help="Experimental local ComfyUI DAG service")
    parser.add_argument("action", choices=("serve", "execute-next", "recover", "list"))
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--store", type=Path)
    parser.add_argument("--key")
    parser.add_argument("--port", type=int, default=8771)


def execute(args: argparse.Namespace) -> int:
    profile = ComfyImageProfile.load(args.profile)
    if (
        set(profile.workflow().image_targets) != {"image"}
        or profile.to_dict()["output"]["mode"] != "RGB"
    ):
        raise ValueError("ComfyUI DAG service requires one image input and RGB output")
    if not 0 <= args.port <= 65535:
        raise ValueError("port must be in 0..65535")
    if args.action == "recover" and not args.key:
        raise ValueError("recover requires --key")
    path = args.directory / "service.sqlite"
    if args.action != "serve" and not path.is_file():
        raise ValueError("ComfyUI service database missing; refusing to recreate")
    if args.action in {"execute-next", "recover"} and args.store is None:
        raise ValueError("ComfyUI execution requires --store")
    if args.action == "recover" and not args.store.is_dir():
        raise ValueError("ComfyUI recovery Store missing")
    owner = RemoteServiceStore(path, profile.identity)
    try:
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
        store = LocalArtifactStore(args.store)
        journal = args.directory / "comfy.sqlite"
        if args.action == "execute-next":
            job = execute_next_image(profile, owner, journal, store)
        else:
            job = recover_image_job(profile, owner, args.key, journal, store)
        print(json.dumps(_job_value(job), indent=2))
        return 0
    except ComfySubmissionUnknown as error:
        print(
            json.dumps(
                {
                    "state": "uncertain",
                    "detail": str(error),
                    "next_action": "list jobs, then recover original key; never resubmit",
                },
                indent=2,
            )
        )
        return 3
    finally:
        owner.close()

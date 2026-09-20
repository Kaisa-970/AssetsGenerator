"""Explicit local ComfyUI profile runner; resume never uploads or submits prompts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .artifact_store import LocalArtifactStore
from .comfy_profile import ComfyImageProfile
from .comfy_submission import ComfySubmissionUnknown
from .models import ArtifactRef
from .remote_protocol import RemoteJob, decode_remote_json
from .remote_service_store import RemoteServiceStore
from .serialization import to_primitive


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "comfy-image", help="Experimental trusted ComfyUI image workflow"
    )
    parser.add_argument("action", choices=("validate", "start", "resume", "status"))
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--store", type=Path)
    parser.add_argument("--key")
    parser.add_argument("--request", type=Path, help="JSON images ArtifactRefs and parameters")


def execute(args: argparse.Namespace) -> int:
    profile = ComfyImageProfile.load(args.profile)
    if args.action == "validate":
        print(
            json.dumps(
                {"identity": to_primitive(profile.identity), "deployment_verified": False}, indent=2
            )
        )
        return 0
    if args.directory is None or not args.key:
        raise ValueError("ComfyUI action requires --directory and --key")
    path = args.directory / "service.sqlite"
    if args.action != "start" and not path.is_file():
        raise ValueError("ComfyUI service database missing; refusing to recreate")
    req = None
    store = None
    if args.action in {"start", "resume"}:
        if args.store is None:
            raise ValueError("ComfyUI execution requires --store")
        if args.action == "resume" and not args.store.is_dir():
            raise ValueError("ComfyUI Artifact Store missing")
        store = LocalArtifactStore(args.store)
    if args.action == "start":
        if args.request is None:
            raise ValueError("ComfyUI start requires --request")
        payload = decode_remote_json(args.request.read_bytes())
        if (
            not isinstance(payload, dict)
            or set(payload) != {"images", "parameters"}
            or not isinstance(payload["images"], dict)
        ):
            raise ValueError("invalid ComfyUI request document")
        images = {}
        for name, value in payload["images"].items():
            if not isinstance(value, dict) or set(value) != {"artifact_id"}:
                raise ValueError("ComfyUI images require ArtifactRef objects")
            images[name] = ArtifactRef(value["artifact_id"])
        req = profile.request(args.key, images=images, parameters=payload["parameters"])
    owner = RemoteServiceStore(path, profile.identity)
    try:
        if args.action == "start":
            assert req is not None and store is not None
            owner.submit(req)
            owner.transition(req, expected="queued", state="running", require_idle=True)
            try:
                profile.start(owner, req, args.directory / "comfy.sqlite", store)
            except ComfySubmissionUnknown:
                # The command reports running/uncertain below. Never call start again.
                raise
        else:
            req = owner.request_for(args.key)
            if req is None:
                raise ValueError("ComfyUI job missing")
        if args.action != "status":
            assert store is not None
            profile.finish(owner, req, args.directory / "comfy.sqlite", store)
        print(json.dumps(_job_value(owner.lookup(req)), indent=2))
        return 0
    except ComfySubmissionUnknown as error:
        print(
            json.dumps(
                {
                    "state": "uncertain",
                    "job": _job_value(owner.lookup(req)) if req else None,
                    "detail": str(error),
                    "next_action": "resume (query only); do not start again",
                },
                indent=2,
            )
        )
        return 3
    finally:
        owner.close()


def _job_value(job: RemoteJob | None) -> dict[str, object] | None:
    if job is None:
        return None
    return {
        "job_id": job.job_id,
        "state": job.state,
        "result": json.loads(job.result_json) if job.result_json else None,
        "error": json.loads(job.error_json) if job.error_json else None,
    }

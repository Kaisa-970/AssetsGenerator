"""Loopback-only interactive candidate alignment and immutable human review records."""

from __future__ import annotations

import argparse
import json
import secrets
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

from .alignment import AlignmentResult, _mesh, align_completion_candidate, candidate_frame
from .artifact_store import LocalArtifactStore
from .completion import _checked
from .contracts import ContractError
from .models import ArtifactRef, SpatialTransform, StructuredValue
from .runtime import utc_now
from .serialization import canonical_json_bytes, to_primitive


class AlignmentReviewSession:
    def __init__(self, store_path: Path, candidate: ArtifactRef, output_root: Path) -> None:
        self.store = LocalArtifactStore(store_path)
        _checked(self.store, candidate, "completion_candidate")
        raw = self.store.read_structured(candidate)
        if (
            raw.get("policy") != "independent-generation-candidate-v1"
            or raw.get("alignment") != "not_performed"
        ):
            raise ContractError("review requires an original independent candidate")
        self.candidate = candidate
        self.output_root = output_root.expanduser().absolute()
        self.source = ArtifactRef(**raw["generated"]["release"])
        self.target = ArtifactRef(**raw["reconstructed"]["release"])
        self.models = {
            "generated": _mesh(self.store, self.source),
            "reconstructed": _mesh(self.store, self.target),
        }
        self.token = secrets.token_urlsafe(32)
        self.saved: dict[str, AlignmentResult] = {}

    def config(self) -> dict[str, Any]:
        return {
            "candidate": to_primitive(self.candidate),
            "token": self.token,
            "source_frame_id": candidate_frame(self.source),
            "target_frame_id": candidate_frame(self.target),
            "target_unit": self.store.get_manifest(
                self.models["reconstructed"].artifact_id
            ).identity.identity_metadata["unit"],
        }

    def save(self, data: dict[str, Any]) -> dict[str, Any]:
        # Frames and filesystem destinations are chosen by the session, never the browser.
        result = align_completion_candidate(
            candidate=self.candidate,
            transform=SpatialTransform(
                candidate_frame(self.source), candidate_frame(self.target), data["matrix"]
            ),
            store_path=self.store.root,
            output_path=self.output_root / f"alignment-{uuid.uuid4().hex}",
        )
        self.saved[result.manifest.artifact_id] = result
        return {
            "alignment": to_primitive(result.manifest),
            "transform": to_primitive(result.transform),
            "output_directory": str(result.output_directory),
        }

    def review(self, data: dict[str, Any]) -> dict[str, Any]:
        result = self.saved.get(data.get("alignment_id", ""))
        if result is None:
            raise ContractError("review must reference an alignment saved in this session")
        decision, reviewer, note = data.get("decision"), data.get("reviewer"), data.get("note", "")
        if decision not in {"accepted", "rejected"}:
            raise ContractError("review decision must be accepted or rejected")
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 200:
            raise ContractError("review requires a reviewer name (at most 200 characters)")
        if not isinstance(note, str) or len(note) > 4000:
            raise ContractError("review note must be text (at most 4000 characters)")
        _checked(self.store, result.manifest, "candidate_alignment")
        raw = self.store.read_structured(result.manifest)
        if self.store.get_build_run(raw["run_id"])["status"] != "succeeded":
            raise ContractError("cannot review an unsuccessful publication")
        value = {
            "schema_version": "1.0",
            "candidate": to_primitive(self.candidate),
            "alignment": to_primitive(result.manifest),
            "transform": to_primitive(result.transform),
            "decision": decision,
            "reviewer": reviewer.strip(),
            "note": note,
            "reviewed_at": utc_now(),
            "reviewer_identity": "self_reported",
            "scope": "manual_alignment_only",
            "fusion": "not_performed",
        }
        ref = self.store.persist_structured(
            StructuredValue("alignment_review", "AlignmentReview", "1.0", value)
        )
        # Append-only, independently identified decisions leave the published package unchanged.
        directory = self.output_root / "reviews"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{ref.artifact_id.split(':')[1]}.json"
        temporary = directory / f".{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_bytes(
                canonical_json_bytes({"review": to_primitive(ref), "record": value})
            )
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return {"review": to_primitive(ref), "record": value}


def create_review_server(session: AlignmentReviewSession, port: int = 8765) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def send_data(self, status: int, data: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def authorized_host(self) -> bool:
            return (
                self.headers.get("Host") == f"127.0.0.1:{cast(HTTPServer, self.server).server_port}"
            )

        def do_GET(self) -> None:
            if not self.authorized_host():
                self.send_error(403)
                return
            if self.path == "/":
                self.send_data(
                    200,
                    files("assets_generator.resources")
                    .joinpath("alignment-editor.html")
                    .read_bytes(),
                    "text/html; charset=utf-8",
                )
            elif self.path == "/session":
                self.send_data(200, canonical_json_bytes(session.config()), "application/json")
            elif self.path in {"/models/generated.glb", "/models/reconstructed.glb"}:
                name = self.path.split("/")[-1].split(".")[0]
                self.send_data(
                    200,
                    session.store.blob_path(session.models[name]).read_bytes(),
                    "model/gltf-binary",
                )
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            origin = f"http://127.0.0.1:{cast(HTTPServer, self.server).server_port}"
            if (
                not self.authorized_host()
                or self.headers.get("Origin") != origin
                or self.headers.get("X-Review-Token") != session.token
            ):
                self.send_error(403)
                return
            if self.path not in {"/save", "/review"}:
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ContractError("request body must be between 1 and 65536 bytes")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ContractError("request must be a JSON object")
                result = session.save(data) if self.path == "/save" else session.review(data)
                self.send_data(200, canonical_json_bytes(result), "application/json")
            except (ValueError, TypeError, KeyError) as exc:
                self.send_data(400, canonical_json_bytes({"error": str(exc)}), "application/json")
            except Exception as exc:
                self.send_data(500, canonical_json_bytes({"error": str(exc)}), "application/json")

    return HTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="人工候选对齐与确认")
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--candidate-ref", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    session = AlignmentReviewSession(
        args.store, ArtifactRef(**json.loads(args.candidate_ref.read_text())), args.output
    )
    server = create_review_server(session, args.port)
    print(f"http://127.0.0.1:{server.server_port}/ (Ctrl+C 关闭)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

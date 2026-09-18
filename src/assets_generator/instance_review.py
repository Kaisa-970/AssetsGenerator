"""Loopback-only visual review for instance proposal selection."""

from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

from .artifact_store import LocalArtifactStore
from .completion import _checked
from .contracts import ContractError
from .instance_proposals import select_instance_proposals
from .models import ArtifactRef
from .serialization import canonical_json_bytes, to_primitive


class InstanceReviewSession:
    def __init__(self, store_path: Path, proposals: ArtifactRef, output_path: Path) -> None:
        self.store = LocalArtifactStore(store_path)
        _checked(self.store, proposals, "instance_proposals")
        raw = self.store.read_structured(proposals)
        if raw.get("status") != "unreviewed_proposals":
            raise ContractError("review requires unreviewed instance proposals")
        image = ArtifactRef(**raw["image"])
        _checked(self.store, image, "rgb_image")
        proposal_items = raw.get("proposals")
        if not isinstance(proposal_items, list):
            raise ContractError("instance proposals require a proposal list")
        masks: list[ArtifactRef] = []
        ids: set[str] = set()
        for item in proposal_items:
            if not isinstance(item, dict) or not isinstance(item.get("proposal_id"), str):
                raise ContractError("instance proposal is missing proposal_id")
            if item["proposal_id"] in ids:
                raise ContractError("instance proposal IDs must be unique")
            ids.add(item["proposal_id"])
            mask = ArtifactRef(**item["mask"])
            _checked(self.store, mask, "binary_mask")
            masks.append(mask)
        self.proposals = proposals
        self.output_path = output_path.expanduser().absolute()
        self.image = image
        self.items = proposal_items
        self.masks = masks
        self.token = secrets.token_urlsafe(32)
        self.published: dict[str, Any] | None = None

    def config(self) -> dict[str, Any]:
        image_metadata = self.store.get_manifest(self.image.artifact_id).identity.identity_metadata
        return {
            "proposals": to_primitive(self.proposals),
            "token": self.token,
            "image": {
                "url": "/image",
                "width": image_metadata.get("width"),
                "height": image_metadata.get("height"),
            },
            "items": [
                {
                    "proposal_id": item["proposal_id"],
                    "mask_url": f"/masks/{index}",
                    "area": item.get("area"),
                    "bbox": item.get("bbox"),
                    "predicted_iou": item.get("predicted_iou"),
                    "stability_score": item.get("stability_score"),
                    "label": item.get("label", "unknown"),
                }
                for index, item in enumerate(self.items)
            ],
            "published": self.published,
        }

    def publish(self, data: dict[str, Any]) -> dict[str, Any]:
        proposal_ids = data.get("proposal_ids")
        reviewer = data.get("reviewer")
        if not isinstance(proposal_ids, list) or not all(
            isinstance(identifier, str) for identifier in proposal_ids
        ):
            raise ContractError("proposal_ids must be a list of proposal IDs")
        if not isinstance(reviewer, str) or len(reviewer) > 200:
            raise ContractError("selection requires reviewer")
        result = select_instance_proposals(
            proposals=self.proposals,
            proposal_ids=proposal_ids,
            reviewer=reviewer,
            store_path=self.store.root,
            output_path=self.output_path,
            invert=data.get("invert", False),
        )
        self.published = result
        return result


def create_instance_review_server(session: InstanceReviewSession, port: int = 8765) -> HTTPServer:
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
                    .joinpath("instance-review.html")
                    .read_bytes(),
                    "text/html; charset=utf-8",
                )
            elif self.path == "/session":
                self.send_data(200, canonical_json_bytes(session.config()), "application/json")
            elif self.path == "/image":
                media_type = session.store.get_manifest(
                    session.image.artifact_id
                ).identity.identity_metadata.get("media_type", "application/octet-stream")
                self.send_data(200, session.store.blob_path(session.image).read_bytes(), media_type)
            elif self.path.startswith("/masks/"):
                try:
                    index = int(self.path.removeprefix("/masks/"))
                    mask = session.masks[index]
                    if index < 0:
                        raise IndexError
                except (ValueError, IndexError):
                    self.send_error(404)
                    return
                self.send_data(200, session.store.blob_path(mask).read_bytes(), "image/png")
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
            if self.path != "/publish":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ContractError("request body must be between 1 and 65536 bytes")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ContractError("request must be a JSON object")
                self.send_data(200, canonical_json_bytes(session.publish(data)), "application/json")
            except (ContractError, ValueError, TypeError, KeyError) as exc:
                self.send_data(400, canonical_json_bytes({"error": str(exc)}), "application/json")
            except FileExistsError as exc:
                self.send_data(409, canonical_json_bytes({"error": str(exc)}), "application/json")
            except Exception as exc:
                self.send_data(500, canonical_json_bytes({"error": str(exc)}), "application/json")

    return HTTPServer(("127.0.0.1", port), Handler)

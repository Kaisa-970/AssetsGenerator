"""Loopback-only visual editing and publication of explicit scene layouts."""

from __future__ import annotations

import json
import math
import re
import secrets
import tempfile
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import numpy as np

from .alignment import _mesh
from .artifact_store import LocalArtifactStore
from .completion import _checked
from .contracts import ContractError
from .errors import classify_error
from .models import ArtifactRef, BuildRun, NodeAttempt, StructuredValue
from .runtime import utc_now
from .scene_workflow import build_scene
from .serialization import canonical_json_bytes, sha256_bytes, to_primitive
from .workflow import _persist_build_run, _persist_provenance


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractError(f"{name} must be finite")
    return float(value)


def _matrix(pose: dict[str, Any]) -> list[list[float]]:
    if not isinstance(pose, dict) or set(pose) != {
        "translation",
        "rotation_degrees",
        "scale",
    }:
        raise ContractError("pose requires translation, rotation_degrees and scale")
    translation = pose["translation"]
    rotation = pose["rotation_degrees"]
    if not isinstance(translation, dict) or set(translation) != {"x", "y", "z"}:
        raise ContractError("translation requires x, y and z")
    if not isinstance(rotation, dict) or set(rotation) != {"yaw", "pitch", "roll"}:
        raise ContractError("rotation_degrees requires yaw, pitch and roll")
    x, y, z = (_number(translation[axis], f"translation.{axis}") for axis in ("x", "y", "z"))
    yaw, pitch, roll = (
        math.radians(_number(rotation[name], f"rotation_degrees.{name}"))
        for name in ("yaw", "pitch", "roll")
    )
    scale = _number(pose["scale"], "scale")
    if scale <= 0:
        raise ContractError("scale must be positive")
    cx, sx = math.cos(roll), math.sin(roll)
    cy, sy = math.cos(pitch), math.sin(pitch)
    cz, sz = math.cos(yaw), math.sin(yaw)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=float)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=float)
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=float)
    result = np.eye(4)
    result[:3, :3] = scale * (rz @ ry @ rx)
    result[:3, 3] = [x, y, z]
    return cast(list[list[float]], result.tolist())


class SceneLayoutReviewSession:
    def __init__(self, store_path: Path, manifest_path: Path, output_path: Path) -> None:
        self.store = LocalArtifactStore(store_path)
        self.manifest_path = manifest_path.expanduser().absolute()
        self.output_path = output_path.expanduser().absolute()
        try:
            draft_bytes = self.manifest_path.read_bytes()
            raw = json.loads(draft_bytes)
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError(f"cannot read scene layout draft: {exc}") from exc
        if not isinstance(raw, dict) or set(raw) != {
            "schema_version",
            "frame_id",
            "unit",
            "instances",
        }:
            raise ContractError(
                "layout draft requires schema_version, frame_id, unit and instances"
            )
        if raw["schema_version"] != "1.0" or raw["unit"] not in {"meter", "relative_unit"}:
            raise ContractError("layout draft requires schema_version 1.0 and meter/relative_unit")
        if not isinstance(raw["frame_id"], str) or not raw["frame_id"].strip():
            raise ContractError("layout draft requires a world frame_id")
        if not isinstance(raw["instances"], list) or not raw["instances"]:
            raise ContractError("layout draft requires nonempty instances")
        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        for item in raw["instances"]:
            if not isinstance(item, dict) or set(item) != {"instance_id", "release"}:
                raise ContractError("draft instance requires only instance_id and release")
            name = item["instance_id"]
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name)
                or name in seen
            ):
                raise ContractError("instance_id must be unique and path-safe")
            seen.add(name)
            if (
                not isinstance(item["release"], dict)
                or set(item["release"]) != {"artifact_id"}
                or not isinstance(item["release"]["artifact_id"], str)
            ):
                raise ContractError("instance release must be an ArtifactRef")
            release = ArtifactRef(**item["release"])
            mesh = _mesh(self.store, release)
            release_raw = self.store.read_structured(release)
            asset_ref = ArtifactRef(**release_raw["asset_definition"])
            _checked(self.store, asset_ref, "asset_definition")
            spatial = self.store.read_structured(asset_ref)["spatial"]
            metadata = self.store.get_manifest(mesh.artifact_id).identity.identity_metadata
            if spatial["unit"] != raw["unit"] or metadata["unit"] != raw["unit"]:
                raise ContractError("layout unit must match each asset")
            items.append(
                {
                    "instance_id": name,
                    "release": to_primitive(release),
                    "model_url": f"/models/{len(items)}.glb",
                    "mesh": mesh,
                    "source_frame_id": (f"{asset_ref.artifact_id}/{spatial['canonical_frame_id']}"),
                }
            )
        self.frame_id = raw["frame_id"].strip()
        self.unit = raw["unit"]
        self.items = items
        self.draft_sha256 = sha256_bytes(draft_bytes)
        self.token = secrets.token_urlsafe(32)
        self.published: dict[str, Any] | None = None

    def config(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "frame_id": self.frame_id,
            "unit": self.unit,
            "instances": [
                {key: item[key] for key in ("instance_id", "release", "model_url")}
                for item in self.items
            ],
            "published": self.published,
        }

    def publish(self, data: dict[str, Any]) -> dict[str, Any]:
        if self.published is not None or self.output_path.exists():
            raise FileExistsError(self.output_path)
        reviewer = data.get("reviewer")
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 200:
            raise ContractError("scene publication requires a reviewer name")
        poses = data.get("poses")
        if not isinstance(poses, dict) or set(poses) != {
            item["instance_id"] for item in self.items
        }:
            raise ContractError("poses must contain every draft instance exactly once")
        instances = []
        for item in self.items:
            name = item["instance_id"]
            instances.append(
                {
                    "instance_id": name,
                    "release": item["release"],
                    "world_pose": {
                        "source_frame_id": item["source_frame_id"],
                        "target_frame_id": self.frame_id,
                        "matrix": _matrix(poses[name]),
                    },
                }
            )
        reviewed_at = utc_now()
        layout = {
            "schema_version": "1.0",
            "frame_id": self.frame_id,
            "unit": self.unit,
            "instances": instances,
        }
        layout_bytes = canonical_json_bytes(layout)
        attempt = NodeAttempt(
            "publish_reviewed_layout",
            1,
            "scene_layout_review@1",
            "core",
            "running",
            "executed",
            utc_now(),
            None,
            None,
        )
        run = BuildRun(
            f"run_{uuid.uuid4().hex}",
            "scene_layout_review",
            "1",
            "running",
            {},
            [attempt],
            utc_now(),
            None,
        )
        _persist_build_run(self.store, run)
        child_run_id = f"run_{uuid.uuid4().hex}"
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            layout_ref = self.store.persist_bytes(
                layout_bytes,
                kind="scene_layout_manifest",
                schema_name="SceneLayoutManifest",
                schema_version="1.0",
                identity_metadata={"media_type": "application/json"},
            )
            run.inputs = {"layout": layout_ref}
            _persist_build_run(self.store, run)
            with tempfile.TemporaryDirectory(
                prefix=f".{self.output_path.name}-review-", dir=self.output_path.parent
            ) as temporary:
                stage = Path(temporary)
                layout_path = stage / "layout.json"
                layout_path.write_bytes(layout_bytes)
                scene_output = stage / "scene"
                scene = build_scene(
                    manifest_path=layout_path,
                    store_path=self.store.root,
                    output_path=scene_output,
                    run_id=child_run_id,
                )
                for child in scene_output.iterdir():
                    child.rename(stage / child.name)
                scene_output.rmdir()
                scene["output_directory"] = str(self.output_path)
                child_run = self.store.persist_structured(
                    StructuredValue(
                        "build_run", "BuildRun", "1.0", self.store.get_build_run(child_run_id)
                    )
                )
                review = self.store.persist_structured(
                    StructuredValue(
                        "scene_layout_review",
                        "SceneLayoutReview",
                        "1.0",
                        {
                            "schema_version": "1.0",
                            "scope": "manual_scene_layout",
                            "reviewer": reviewer.strip(),
                            "reviewer_identity": "self_reported",
                            "reviewed_at": reviewed_at,
                            "draft_sha256": self.draft_sha256,
                            "layout_sha256": sha256_bytes(layout_bytes),
                            "layout": layout,
                            "layout_artifact": to_primitive(layout_ref),
                            "scene": scene["scene"],
                            "scene_run_id": scene["run_id"],
                            "instances": [item["instance_id"] for item in self.items],
                            "pose_status": "user_supplied_unverified",
                        },
                    )
                )
                provenance = _persist_provenance(
                    self.store,
                    run_id=run.run_id,
                    node_id=attempt.node_id,
                    port_name="review",
                    artifact=review,
                    derived_from=[layout_ref, ArtifactRef(**scene["scene"]), child_run],
                    operator="scene_layout_review",
                    backend="core",
                    backend_version="1",
                    parameters={
                        "reviewer": reviewer.strip(),
                        "reviewer_identity": "self_reported",
                    },
                    seed=None,
                    source="user",
                )
                attempt.outputs = {
                    "review": review,
                    "scene": ArtifactRef(**scene["scene"]),
                    "child_run": child_run,
                    "provenance": provenance,
                }
                attempt.status = "succeeded"
                attempt.finished_at = utc_now()
                run.status = "succeeded"
                run.finished_at = attempt.finished_at
                run_ref = _persist_build_run(self.store, run)
                (stage / "layout-review.json").write_bytes(
                    self.store.blob_path(review).read_bytes()
                )
                (stage / "layout-review-ref.json").write_bytes(
                    canonical_json_bytes(to_primitive(review))
                )
                (stage / "layout-provenance.json").write_bytes(
                    self.store.blob_path(provenance).read_bytes()
                )
                (stage / "layout-run.json").write_bytes(self.store.blob_path(run_ref).read_bytes())
                result = {
                    **scene,
                    "layout_manifest": str(self.output_path / "layout.json"),
                    "layout_review": to_primitive(review),
                    "layout_run_id": run.run_id,
                }
                (stage / "result.json").write_bytes(canonical_json_bytes(result))
                stage.rename(self.output_path)
            self.published = result
            return result
        except Exception as error:
            if (self.store.root / "runs" / f"{child_run_id}.json").exists():
                attempt.outputs["child_run"] = self.store.persist_structured(
                    StructuredValue(
                        "build_run", "BuildRun", "1.0", self.store.get_build_run(child_run_id)
                    )
                )
            attempt.status = "failed"
            attempt.error_code = classify_error(error).value
            attempt.finished_at = utc_now()
            run.status = "failed"
            run.finished_at = attempt.finished_at
            _persist_build_run(self.store, run)
            raise


def create_scene_layout_review_server(
    session: SceneLayoutReviewSession, port: int = 8765
) -> HTTPServer:
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
                    .joinpath("scene-layout-editor.html")
                    .read_bytes(),
                    "text/html; charset=utf-8",
                )
            elif self.path == "/session":
                self.send_data(200, canonical_json_bytes(session.config()), "application/json")
            elif self.path.startswith("/models/") and self.path.endswith(".glb"):
                try:
                    index = int(self.path.removeprefix("/models/").removesuffix(".glb"))
                    if index < 0:
                        raise IndexError
                    mesh = session.items[index]["mesh"]
                except (ValueError, IndexError):
                    self.send_error(404)
                    return
                self.send_data(200, session.store.blob_path(mesh).read_bytes(), "model/gltf-binary")
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

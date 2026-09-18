from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import (
    ARTIFACT_KINDS,
    ArtifactAnnotations,
    ArtifactIdentity,
    ArtifactManifest,
    ArtifactRef,
    BlobIdentity,
    BlobLocation,
    StructuredValue,
)
from .serialization import canonical_json_bytes, read_json, sha256_bytes


class ArtifactStoreError(RuntimeError):
    pass


def _digest_path(root: Path, digest: str) -> Path:
    algorithm, value = digest.split(":", 1)
    if algorithm != "sha256" or len(value) != 64:
        raise ArtifactStoreError(f"unsupported digest: {digest}")
    return root / value[:2] / value


def create_manifest(
    blob: BlobIdentity,
    *,
    kind: str,
    schema_name: str,
    schema_version: str,
    identity_metadata: dict[str, Any] | None = None,
    annotations: ArtifactAnnotations | None = None,
) -> ArtifactManifest:
    if kind not in ARTIFACT_KINDS:
        raise ArtifactStoreError(f"unknown artifact kind: {kind}")
    identity = ArtifactIdentity(
        kind=kind,
        schema_name=schema_name,
        schema_version=schema_version,
        blob_digest=blob.digest,
        identity_metadata=identity_metadata or {},
    )
    artifact_id = sha256_bytes(canonical_json_bytes(identity))
    return ArtifactManifest(artifact_id, identity, annotations or ArtifactAnnotations())


@dataclass
class StoreTransaction:
    store: LocalArtifactStore
    transaction_id: str
    path: Path
    committed: bool = False

    def put_blob(self, data: bytes) -> BlobIdentity:
        blob = BlobIdentity(sha256_bytes(data), len(data))
        target = _digest_path(self.path / "blobs", blob.digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return blob

    def put_manifest(self, manifest: ArtifactManifest) -> None:
        expected = sha256_bytes(canonical_json_bytes(manifest.identity))
        if expected != manifest.artifact_id:
            raise ArtifactStoreError("artifact manifest identity digest mismatch")
        target = _digest_path(self.path / "manifests", manifest.artifact_id).with_suffix(".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(canonical_json_bytes(manifest))

    def commit(self) -> None:
        if self.committed:
            raise ArtifactStoreError("transaction already committed")
        manifests = (
            list((self.path / "manifests").rglob("*.json"))
            if (self.path / "manifests").exists()
            else []
        )
        for path in manifests:
            raw = read_json(path)
            digest = raw["identity"]["blob_digest"]
            staged_blob = _digest_path(self.path / "blobs", digest)
            final_blob = _digest_path(self.store.blobs_dir, digest)
            if not staged_blob.exists() and not final_blob.exists():
                raise ArtifactStoreError(f"manifest references missing blob: {digest}")
            if staged_blob.exists() and sha256_bytes(staged_blob.read_bytes()) != digest:
                raise ArtifactStoreError(f"staged blob digest mismatch: {digest}")
        self.store._publish_tree(self.path / "blobs", self.store.blobs_dir)
        self.store._publish_manifests(self.path / "manifests")
        self.committed = True
        shutil.rmtree(self.path, ignore_errors=True)

    def abort(self) -> None:
        if not self.committed:
            shutil.rmtree(self.path, ignore_errors=True)

    def __enter__(self) -> StoreTransaction:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is not None or not self.committed:
            self.abort()


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.blobs_dir = self.root / "blobs" / "sha256"
        self.manifests_dir = self.root / "manifests" / "sha256"
        self.staging_dir = self.root / "staging"
        self.cache_dir = self.root / "cache" / "sha256"
        for path in (self.blobs_dir, self.manifests_dir, self.staging_dir, self.cache_dir):
            path.mkdir(parents=True, exist_ok=True)

    def transaction(self) -> StoreTransaction:
        transaction_id = uuid.uuid4().hex
        path = self.staging_dir / transaction_id
        path.mkdir(parents=True)
        return StoreTransaction(self, transaction_id, path)

    def persist_bytes(
        self,
        data: bytes,
        *,
        kind: str,
        schema_name: str,
        schema_version: str,
        identity_metadata: dict[str, Any] | None = None,
        annotations: ArtifactAnnotations | None = None,
    ) -> ArtifactRef:
        with self.transaction() as transaction:
            blob = transaction.put_blob(data)
            manifest = create_manifest(
                blob,
                kind=kind,
                schema_name=schema_name,
                schema_version=schema_version,
                identity_metadata=identity_metadata,
                annotations=annotations,
            )
            transaction.put_manifest(manifest)
            transaction.commit()
        return ArtifactRef(manifest.artifact_id)

    def persist_structured(self, value: StructuredValue) -> ArtifactRef:
        return self.persist_bytes(
            canonical_json_bytes(value.value),
            kind=value.kind,
            schema_name=value.schema_name,
            schema_version=value.schema_version,
            identity_metadata={"media_type": "application/json"},
        )

    def get_manifest(self, artifact_id: str) -> ArtifactManifest:
        raw = read_json(_digest_path(self.manifests_dir, artifact_id).with_suffix(".json"))
        identity = ArtifactIdentity(**raw["identity"])
        expected = sha256_bytes(canonical_json_bytes(identity))
        if raw["artifact_id"] != artifact_id or expected != artifact_id:
            raise ArtifactStoreError(f"artifact manifest identity mismatch: {artifact_id}")
        annotations = ArtifactAnnotations(**raw["annotations"])
        return ArtifactManifest(raw["artifact_id"], identity, annotations)

    def resolve_blob(self, digest: str) -> list[BlobLocation]:
        path = _digest_path(self.blobs_dir, digest)
        return [BlobLocation(digest, path.resolve().as_uri())] if path.is_file() else []

    def blob_path(self, artifact: ArtifactRef) -> Path:
        return _digest_path(
            self.blobs_dir, self.get_manifest(artifact.artifact_id).identity.blob_digest
        )

    def read_structured(self, artifact: ArtifactRef) -> dict[str, Any]:
        value = json.loads(self.blob_path(artifact).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ArtifactStoreError("structured artifact does not contain a JSON object")
        return value

    def verify_digest(self, artifact: ArtifactRef) -> bool:
        try:
            manifest = self.get_manifest(artifact.artifact_id)
            path = _digest_path(self.blobs_dir, manifest.identity.blob_digest)
            return (
                path.is_file() and sha256_bytes(path.read_bytes()) == manifest.identity.blob_digest
            )
        except (ArtifactStoreError, FileNotFoundError, KeyError, TypeError, ValueError):
            return False

    def find_artifacts(self, kind: str) -> list[ArtifactRef]:
        found: list[ArtifactRef] = []
        for path in self.manifests_dir.rglob("*.json"):
            raw = read_json(path)
            if raw.get("identity", {}).get("kind") == kind:
                reference = ArtifactRef(str(raw["artifact_id"]))
                try:
                    self.get_manifest(reference.artifact_id)
                except ArtifactStoreError:
                    continue
                else:
                    found.append(reference)
        return found

    def record_build_run(self, run_id: str, value: StructuredValue) -> ArtifactRef:
        if (self.root / "run_owners" / f"{run_id}.json").exists():
            raise ArtifactStoreError("reserved workbench child requires the owned durable writer")
        reference = self.persist_structured(value)
        index_dir = self.root / "runs"
        index_dir.mkdir(parents=True, exist_ok=True)
        target = index_dir / f"{run_id}.json"
        temporary = index_dir / f".{run_id}.{uuid.uuid4().hex}.tmp"
        temporary.write_bytes(canonical_json_bytes(reference))
        os.replace(temporary, target)
        return reference

    def get_build_run(self, run_id: str) -> dict[str, Any]:
        raw = read_json(self.root / "runs" / f"{run_id}.json")
        reference = ArtifactRef(str(raw["artifact_id"]))
        return self.read_structured(reference)

    def get_cache(self, cache_key: str) -> dict[str, Any] | None:
        path = _digest_path(self.cache_dir, cache_key).with_suffix(".json")
        if not path.is_file():
            return None
        value = read_json(path)
        if value.get("cache_key") != cache_key:
            return None
        artifacts = value.get("artifact_ids", [])
        if not isinstance(artifacts, list):
            return None
        if not all(self.verify_digest(ArtifactRef(str(item))) for item in artifacts):
            return None
        return value

    def put_cache(self, cache_key: str, value: dict[str, Any]) -> None:
        target = _digest_path(self.cache_dir, cache_key).with_suffix(".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(canonical_json_bytes({**value, "cache_key": cache_key}))
        os.replace(temporary, target)

    @staticmethod
    def _publish_tree(source: Path, destination: Path) -> None:
        if not source.exists():
            return
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.read_bytes() != path.read_bytes():
                    raise ArtifactStoreError(f"identity collision at {target}")
                continue
            os.replace(path, target)

    def _publish_manifests(self, source: Path) -> None:
        if not source.exists():
            return
        for path in source.rglob("*.json"):
            raw = read_json(path)
            artifact_id = str(raw["artifact_id"])
            target = _digest_path(self.manifests_dir, artifact_id).with_suffix(".json")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                existing = read_json(target)
                if existing["identity"] != raw["identity"]:
                    raise ArtifactStoreError(f"identity collision at {target}")
                continue
            os.replace(path, target)

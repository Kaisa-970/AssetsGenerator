"""Durable one-shot submission boundary for non-idempotent ComfyUI POST /prompt.

This journal does not implement transport, history lookup or a DAG adapter. Once
sending is committed, callers may only observe the original prompt, never repost.
"""

from __future__ import annotations

import fcntl
import json
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .serialization import canonical_json_bytes, sha256_bytes


class ComfySubmissionUnknown(ValueError):
    """A prompt may have been received; only query the recorded prompt ID."""


class ComfySubmissionJournal:
    def __init__(self, path: Path, *, expected_journal_id: str | None = None):
        from .workbench_persistence import DurableIO

        path = path.expanduser().absolute()
        if expected_journal_id is not None:
            if str(uuid.UUID(expected_journal_id)) != expected_journal_id:
                raise ValueError("invalid expected ComfyUI journal ID")
            if not path.is_file():
                raise ValueError("expected ComfyUI journal missing; refusing to recreate")
        io = DurableIO()
        io.mkdir(path.parent)
        marker = path.with_name(path.name + ".identity.json")
        with path.with_name(path.name + ".init.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            fresh = not marker.exists() and not path.exists()
            if fresh and expected_journal_id is not None:
                raise ValueError("expected ComfyUI journal missing; refusing to recreate")
            if fresh:
                identity = {"schema": "comfy-journal@1", "journal_id": str(uuid.uuid4())}
                io.write(marker, canonical_json_bytes(identity), exclusive=True)
            else:
                if not marker.is_file() or not path.is_file():
                    raise ValueError("ComfyUI journal or identity missing; refusing to recreate")
                identity = json.loads(marker.read_bytes())
                if (
                    not isinstance(identity, dict)
                    or set(identity) != {"schema", "journal_id"}
                    or identity["schema"] != "comfy-journal@1"
                    or str(uuid.UUID(identity["journal_id"])) != identity["journal_id"]
                ):
                    raise ValueError("invalid ComfyUI journal identity")
            if expected_journal_id is not None and identity["journal_id"] != expected_journal_id:
                raise ValueError("ComfyUI journal differs from parent binding")
            self.journal_id: str = identity["journal_id"]
            self.db = sqlite3.connect(
                path.as_uri() + ("?mode=rwc" if fresh else "?mode=rw"),
                uri=True,
                isolation_level=None,
            )
            try:
                if fresh:
                    self.db.execute("PRAGMA journal_mode=WAL")
                    self.db.execute("PRAGMA synchronous=FULL")
                    self._initialize()
                    self.db.execute("CREATE TABLE journal_identity (body BLOB NOT NULL)")
                    self.db.execute(
                        "INSERT INTO journal_identity VALUES (?)", (canonical_json_bytes(identity),)
                    )
                    io.sync_directory(path.parent)
                else:
                    rows = self.db.execute("SELECT body FROM journal_identity").fetchall()
                    if rows != [(canonical_json_bytes(identity),)]:
                        raise ValueError("ComfyUI journal identity mismatch")
                    tables = {
                        row[0]
                        for row in self.db.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                    if not {"prompts", "observations", "image_imports"} <= tables:
                        raise ValueError("ComfyUI journal tables missing; refusing to recreate")
                    self.db.execute("PRAGMA synchronous=FULL")
            except BaseException:
                self.db.close()
                raise

    def _initialize(self) -> None:
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS prompts ("
            "submission_key TEXT PRIMARY KEY, request BLOB NOT NULL, "
            "prompt_id TEXT UNIQUE NOT NULL, phase TEXT NOT NULL)"
        )

        self.db.execute(
            "CREATE TABLE IF NOT EXISTS observations ("
            "submission_key TEXT PRIMARY KEY, body BLOB NOT NULL, digest TEXT NOT NULL)"
        )

        self.db.execute(
            "CREATE TABLE IF NOT EXISTS image_imports ("
            "submission_key TEXT NOT NULL, node TEXT NOT NULL, slot INTEGER NOT NULL, "
            "receipt BLOB NOT NULL, digest TEXT NOT NULL, "
            "PRIMARY KEY (submission_key, node, slot))"
        )

    def close(self) -> None:
        self.db.close()

    def submission_binding(self, key: str) -> dict[str, str]:
        """Parent must persist this binding before authorizing upstream submission."""
        record = self.read(key)
        return {
            "journal_id": self.journal_id,
            "submission_key": key,
            "prompt_id": record["prompt_id"],
            "request_digest": sha256_bytes(
                canonical_json_bytes(
                    {
                        "deployment": record["deployment"],
                        "prompt": record["prompt"],
                    }
                )
            ),
        }

    def verify_binding(self, binding: dict[str, str]) -> None:
        """Read only: deleted/replaced rows must not be prepared again on recovery."""
        if set(binding) != {"journal_id", "submission_key", "prompt_id", "request_digest"}:
            raise ValueError("invalid ComfyUI parent binding")
        if self.submission_binding(binding["submission_key"]) != binding:
            raise ValueError("ComfyUI submission differs from parent binding")

    def prepare(
        self, key: str, *, deployment: dict[str, Any], prompt: dict[str, Any]
    ) -> dict[str, Any]:
        """Reserve exact API graph and deployment before permitting any network call.

        deployment must include the verified service/profile identity and input
        mappings; this layer binds bytes, not the truth of that external evidence.
        """
        if not isinstance(key, str) or not key or len(key) > 128:
            raise ValueError("invalid ComfyUI submission key")
        if not isinstance(deployment, dict) or not deployment:
            raise ValueError("ComfyUI deployment identity required")
        if not isinstance(prompt, dict) or not prompt:
            raise ValueError("ComfyUI API prompt required")
        request = canonical_json_bytes({"deployment": deployment, "prompt": prompt})
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT request FROM prompts WHERE submission_key=?", (key,)
            ).fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO prompts VALUES (?, ?, ?, 'prepared')",
                    (key, request, str(uuid.uuid4())),
                )
            elif bytes(row[0]) != request:
                raise ValueError("ComfyUI submission identity conflict")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return self.read(key)

    def read(self, key: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT request, prompt_id, phase FROM prompts WHERE submission_key=?", (key,)
        ).fetchone()
        if row is None:
            raise ValueError("ComfyUI submission missing; refusing to recreate")
        request, prompt_id, phase = row
        parsed = json.loads(request)
        if (
            canonical_json_bytes(parsed) != bytes(request)
            or set(parsed) != {"deployment", "prompt"}
            or str(uuid.UUID(prompt_id)) != prompt_id
            or phase not in {"prepared", "sending", "acknowledged", "observed"}
        ):
            raise ValueError("invalid ComfyUI submission record")
        return {**parsed, "prompt_id": prompt_id, "phase": phase}

    def submit_once(
        self, key: str, post: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> dict[str, Any]:
        """post must perform one HTTP attempt, with transport retries disabled."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            record = self.read(key)
            if record["phase"] == "sending":
                raise ComfySubmissionUnknown("query original prompt_id; resubmission forbidden")
            if record["phase"] in {"acknowledged", "observed"}:
                if record["phase"] == "observed":
                    self.observation(key)
                self.db.execute("COMMIT")
                return record
            self.db.execute("UPDATE prompts SET phase='sending' WHERE submission_key=?", (key,))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        # Even an interruption immediately before this call leaves sending durable.
        # False uncertainty is preferable to a duplicate untraceable model execution.
        try:
            response = post({"prompt_id": record["prompt_id"], "prompt": record["prompt"]})
            if (
                not isinstance(response, dict)
                or response.get("prompt_id") != record["prompt_id"]
                or response.get("error") is not None
                or response.get("node_errors") not in ({}, None)
            ):
                raise ValueError("ComfyUI acknowledgement does not match reserved prompt")
        except Exception as error:
            raise ComfySubmissionUnknown(
                "ComfyUI submission outcome unknown; query only"
            ) from error
        self.db.execute(
            "UPDATE prompts SET phase='acknowledged' WHERE submission_key=? AND phase='sending'",
            (key,),
        )
        return self.read(key)

    def observation(self, key: str) -> dict[str, Any] | None:
        record = self.read(key)
        row = self.db.execute(
            "SELECT body, digest FROM observations WHERE submission_key=?", (key,)
        ).fetchone()
        if record["phase"] != "observed":
            if row is not None:
                raise ValueError("ComfyUI observation without committed phase")
            return None
        if row is None or sha256_bytes(bytes(row[0])) != row[1]:
            raise ValueError("ComfyUI fixed observation missing or corrupt")
        from .comfy_history import validate_history

        raw = json.loads(row[0])
        validated = validate_history({**record, "phase": "sending"}, raw)
        return validated

    def record_history(self, key: str, history: dict[str, Any]) -> dict[str, Any]:
        from .comfy_history import validate_history

        body = canonical_json_bytes(history)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            record = self.read(key)
            if record["phase"] == "observed":
                result = self.observation(key)
                previous = self.db.execute(
                    "SELECT body FROM observations WHERE submission_key=?", (key,)
                ).fetchone()
                if bytes(previous[0]) != body:
                    raise ValueError("ComfyUI fixed observation conflict")
                assert result is not None
            else:
                result = validate_history(record, json.loads(body))
                self.db.execute(
                    "INSERT INTO observations VALUES (?, ?, ?)", (key, body, sha256_bytes(body))
                )
                self.db.execute(
                    "UPDATE prompts SET phase='observed' WHERE submission_key=?", (key,)
                )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return result

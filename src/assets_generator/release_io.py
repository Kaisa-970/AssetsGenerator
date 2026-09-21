"""Release file validation and atomic materialization shared by workflows."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath

from .artifact_store import LocalArtifactStore
from .contracts import ContractError
from .models import ArtifactRef, AssetRelease
from .publication import publish_staged_release


def release_files(store: LocalArtifactStore, release: ArtifactRef) -> dict[str, ArtifactRef]:
    """Validate complete release files before adding them to an instance directory."""
    raw = store.read_structured(release)["files"]
    result = {}
    for name, value in raw.items():
        path = PurePosixPath(name)
        if (
            not name
            or name == "."
            or path.is_absolute()
            or PureWindowsPath(name).drive
            or ".." in path.parts
            or "\\" in name
            or "\x00" in name
            or path.as_posix() != name
            or path.parts[0] in {"asset.json", "release.json", "run.json"}
        ):
            raise ContractError(f"unsafe scene asset release path: {name}")
        ref = ArtifactRef(**value)
        if not store.verify_digest(ref):
            raise ContractError(f"invalid scene asset release file: {name}")
        result[name] = ref
    for name in result:
        if any(parent.as_posix() in result for parent in PurePosixPath(name).parents):
            raise ContractError(f"conflicting scene asset release path: {name}")
    return result


def materialize_json(store: LocalArtifactStore, artifact: ArtifactRef, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(store.blob_path(artifact).read_bytes())


def materialize_release(
    store: LocalArtifactStore,
    output_path: Path,
    release_ref: ArtifactRef,
    release: AssetRelease,
    run_ref: ArtifactRef,
) -> None:
    output_path = output_path.expanduser().absolute()
    if output_path.exists():
        raise FileExistsError(f"output directory already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.parent / f".{output_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.mkdir()
        materialize_json(store, release.asset_definition, temporary / "asset.json")
        materialize_json(store, release_ref, temporary / "release.json")
        materialize_json(store, run_ref, temporary / "run.json")
        for relative_path, reference in release.files.items():
            target = temporary / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(store.blob_path(reference), target)
            if not store.verify_digest(reference):
                raise RuntimeError(f"artifact verification failed during release: {relative_path}")
        expected = {"asset.json", "release.json", "run.json", *release.files.keys()}
        actual = {
            str(path.relative_to(temporary)) for path in temporary.rglob("*") if path.is_file()
        }
        if actual != expected:
            raise RuntimeError("materialized release does not match its manifest")
        publish_staged_release(temporary, output_path)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

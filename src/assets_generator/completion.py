"""Explicit generation candidates alongside immutable reconstruction evidence.

This is candidate preparation, not mesh completion or geometric fusion.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .artifact_store import LocalArtifactStore
from .backend_registry import ResolvedPlan
from .contracts import ContractError
from .models import SCHEMA_VERSION, ArtifactRef, StructuredValue
from .observations import observation_bundle_from_artifact
from .serialization import canonical_json_bytes, to_primitive
from .workflow import BuildResult, build_image_asset


@dataclass(frozen=True)
class CompletionCandidateResult:
    manifest: ArtifactRef
    generated: BuildResult
    output_directory: Path


def _checked(store: LocalArtifactStore, ref: ArtifactRef, kind: str) -> None:
    if not store.verify_digest(ref) or store.get_manifest(ref.artifact_id).identity.kind != kind:
        raise ContractError(f"candidate requires valid {kind} artifact")


def build_completion_candidate(
    *,
    observations: ArtifactRef,
    reconstruction_release: ArtifactRef,
    view_id: str,
    store_path: Path,
    output_path: Path,
    resolved_plan: ResolvedPlan,
    seed: int = 42,
    pipeline_type: str = "512",
) -> CompletionCandidateResult:
    """Generate an unaligned alternative from one explicitly selected masked view.

    Both assets remain separate. Reconstruction geometry is never a model input.
    The new manifest records the association, not a false geometric dependency.
    """
    store = LocalArtifactStore(store_path)
    bundle = observation_bundle_from_artifact(observations, store)
    selected = next((view for view in bundle.views if view.view_id == view_id), None)
    if selected is None or selected.mask is None:
        raise ContractError("candidate requires an existing view_id with a provided mask")
    _checked(store, reconstruction_release, "asset_release")
    release = store.read_structured(reconstruction_release)
    asset_ref = ArtifactRef(**release["asset_definition"])
    _checked(store, asset_ref, "asset_definition")
    asset = store.read_structured(asset_ref)
    if bundle.observation_id not in asset["source_observation_ids"]:
        raise ContractError("reconstruction release does not reference the observation bundle")
    components = asset.get("component_provenance", [])
    if not components or any(c["source"] != "reconstructed" for c in components):
        raise ContractError("candidate requires a reconstructed source asset")
    files = release["files"]
    if "geometry/visual.glb" not in files:
        raise ContractError("reconstruction release lacks visual GLB")
    for name, raw in files.items():
        path = PurePosixPath(name)
        if (
            not name
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or path.as_posix() != name
            or name in {"asset.json", "release.json"}
        ):
            raise ContractError("unsafe reconstruction release path")
        ref = ArtifactRef(**raw)
        if not store.verify_digest(ref):
            raise ContractError(f"invalid reconstruction release file: {name}")
    output = output_path.expanduser().absolute()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temp:
        stage = Path(temp)
        generated = build_image_asset(
            image_path=store.blob_path(selected.image),
            mask_path=store.blob_path(selected.mask),
            store_path=store_path,
            output_path=stage / "generated",
            resolved_plan=resolved_plan,
            seed=seed,
            pipeline_type=pipeline_type,
            asset_name=f"generated candidate for {view_id}",
        )
        original = stage / "reconstructed"
        original.mkdir()
        for name, ref in {
            "asset.json": to_primitive(asset_ref),
            "release.json": to_primitive(reconstruction_release),
            **files,
        }.items():
            target = original / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(store.blob_path(ArtifactRef(**ref)), target)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "policy": "independent-generation-candidate-v1",
            "status": "candidate_only",
            "observations": to_primitive(observations),
            "selected_view_id": view_id,
            "selected_image": to_primitive(selected.image),
            "selected_mask": to_primitive(selected.mask),
            "reconstructed": {
                "release": to_primitive(reconstruction_release),
                "source": "reconstructed",
                "directory": "reconstructed",
            },
            "generated": {
                "release": to_primitive(generated.release_manifest),
                "source": "generated",
                "directory": "generated",
                "run_id": generated.run_id,
            },
            "geometry_conditioning": False,
            "alignment": "not_performed",
            "fusion": "not_performed",
            "observed_surface_preservation": "not_guaranteed",
            "review_status": "pending",
        }
        reference = store.persist_structured(
            StructuredValue("completion_candidate", "CompletionCandidate", SCHEMA_VERSION, manifest)
        )
        (stage / "candidate.json").write_bytes(canonical_json_bytes(manifest))
        (stage / "candidate-ref.json").write_bytes(canonical_json_bytes(to_primitive(reference)))
        stage.rename(output)
    generated = BuildResult(
        generated.run_id,
        generated.asset_definition,
        generated.release_manifest,
        generated.glb,
        generated.quality_report,
        output / "generated",
    )
    return CompletionCandidateResult(reference, generated, output)

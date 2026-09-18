from __future__ import annotations

import numpy as np
import pytest
import trimesh
from PIL import Image

from assets_generator.contracts import ContractError
from assets_generator.usd_writer import principal_inertia, sdk_environment_identity, write_usd

pytest.importorskip("pxr")


def _physics(tensor=None):
    return {
        "mass_kg": 2.5,
        "center_of_mass_m": [0.1, -0.2, 0.3],
        "inertia_kg_m2": tensor
        if tensor is not None
        else [[1.2, 0.1, 0], [0.1, 1.3, 0.1], [0, 0.1, 1.4]],
        "static_friction": 0.6,
        "dynamic_friction": 0.4,
        "restitution": 0.2,
    }


@pytest.mark.parametrize(
    "tensor",
    [np.eye(3), np.diag([1, 1, 2]), np.array([[1.2, 0.1, 0], [0.1, 1.3, 0.1], [0, 0.1, 1.4]])],
)
def test_principal_inertia_deterministic_roundtrip(tensor):
    values, axes = principal_inertia(tensor)
    assert np.allclose(axes @ np.diag(values) @ axes.T, tensor)
    assert np.linalg.det(axes) > 0
    assert np.array_equal(principal_inertia(tensor)[1], axes)


def test_usd_roundtrip_preserves_physics_instances_and_colors(tmp_path):
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade

    mesh = trimesh.creation.box()
    mesh.visual.vertex_colors = np.tile([40, 80, 120, 255], (len(mesh.vertices), 1))
    scene = trimesh.Scene()
    scene.add_geometry(mesh, node_name="a", geom_name="box")
    scene.graph.update(
        frame_to="b", matrix=trimesh.transformations.translation_matrix([2, 3, 4]), geometry="box"
    )
    result = write_usd(
        visual=scene,
        collision=trimesh.Scene(trimesh.creation.box()),
        physics=_physics(),
        output_path=tmp_path / "asset.usda",
    )
    assert result["status"] == "pass"
    stage = Usd.Stage.Open(str(tmp_path / "asset.usda"))
    shader = UsdShade.Shader(stage.GetPrimAtPath("/Asset/Materials/Visual_0/Surface"))
    assert shader.GetInput("metallic").Get() == 1.0
    assert stage.GetPrimAtPath("/Asset/Visual/Node_1/Mesh").IsInstance()
    assert (
        UsdGeom.Mesh(stage.GetPrimAtPath("/Asset/Collision/Node_0/Mesh")).GetPurposeAttr().Get()
        == "guide"
    )
    colors = (
        UsdGeom.PrimvarsAPI(stage.GetPrimAtPath("/Asset/Visual/Node_0/Mesh"))
        .GetPrimvar("displayColor")
        .Get()
    )
    assert np.allclose(colors, np.tile([40, 80, 120], (len(mesh.vertices), 1)) / 255)
    assert (
        UsdPhysics.MeshCollisionAPI(stage.GetPrimAtPath("/Asset/Collision/Node_0/Mesh"))
        .GetApproximationAttr()
        .Get()
        == "convexHull"
    )


def test_usd_texture_and_factor(tmp_path):
    from pxr import Usd, UsdGeom, UsdShade

    mesh = trimesh.creation.box()
    mesh.visual = trimesh.visual.texture.TextureVisuals(
        uv=np.zeros((len(mesh.vertices), 2)),
        material=trimesh.visual.material.PBRMaterial(
            baseColorTexture=Image.new("RGB", (2, 2), "red"),
            baseColorFactor=[128, 255, 255, 255],
            metallicFactor=0.3,
            roughnessFactor=0.7,
        ),
    )
    result = write_usd(
        visual=trimesh.Scene(mesh),
        collision=trimesh.Scene(trimesh.creation.box()),
        physics=_physics(np.eye(3).tolist()),
        output_path=tmp_path / "asset.usda",
    )
    assert result["textures"] == ["textures/base_color_0.png"]
    assert Image.open(tmp_path / result["textures"][0]).getpixel((0, 0)) == (255, 0, 0)
    stage = Usd.Stage.Open(str(tmp_path / "asset.usda"))
    texture = UsdShade.Shader(stage.GetPrimAtPath("/Asset/Materials/Visual_0/Texture"))
    assert texture.GetInput("file").Get().path == "textures/base_color_0.png"
    assert texture.GetInput("wrapS").Get() == "repeat"
    assert texture.GetInput("wrapT").Get() == "repeat"
    assert np.isclose(texture.GetInput("scale").Get()[0], 128 / 255)
    assert len(
        UsdGeom.PrimvarsAPI(stage.GetPrimAtPath("/Asset/Visual/Node_0/Mesh")).GetPrimvar("st").Get()
    ) == len(mesh.vertices)


def test_usd_unsupported_material_fails_before_output(tmp_path):
    mesh = trimesh.creation.box()
    mesh.visual = trimesh.visual.texture.TextureVisuals(
        material=trimesh.visual.material.PBRMaterial(normalTexture=Image.new("RGB", (2, 2)))
    )
    with pytest.raises(ContractError, match="unsupported PBR"):
        write_usd(
            visual=trimesh.Scene(mesh),
            collision=trimesh.Scene(mesh),
            physics=_physics(),
            output_path=tmp_path / "bad.usda",
        )
    assert not (tmp_path / "bad.usda").exists()


def test_sdk_environment_has_content_identity():
    identity = sdk_environment_identity()
    assert identity["usd_core_version"]
    assert identity["installed_content_digest"].startswith("sha256:")


def test_rotated_repeated_eigenspace_roundtrip(tmp_path):
    rotation = trimesh.transformations.rotation_matrix(0.71, [1, 2, 3])[:3, :3]
    tensor = rotation @ np.diag([1.0, 1.0, 1.7]) @ rotation.T
    values, axes = principal_inertia(tensor)
    assert np.allclose(axes @ np.diag(values) @ axes.T, tensor, atol=1e-12)
    result = write_usd(
        visual=trimesh.Scene(trimesh.creation.box()),
        collision=trimesh.Scene(trimesh.creation.box()),
        physics=_physics(tensor.tolist()),
        output_path=tmp_path / "repeated.usda",
    )
    assert result["inertia_max_error"] < 1e-6


@pytest.mark.parametrize("feature", ["emissiveFactor", "metallicRoughnessTexture", "alphaMode"])
def test_unsupported_pbr_features_are_explicit(tmp_path, feature):
    values = {
        "emissiveFactor": [0.2, 0.0, 0.0],
        "metallicRoughnessTexture": Image.new("RGB", (2, 2)),
        "alphaMode": "BLEND",
    }
    mesh = trimesh.creation.box()
    mesh.visual = trimesh.visual.texture.TextureVisuals(
        material=trimesh.visual.material.PBRMaterial(**{feature: values[feature]})
    )
    with pytest.raises(ContractError, match="unsupported|opaque"):
        write_usd(
            visual=trimesh.Scene(mesh),
            collision=trimesh.Scene(trimesh.creation.box()),
            physics=_physics(),
            output_path=tmp_path / "unsupported.usda",
        )


def test_no_material_uses_default_pbr_factor(tmp_path):
    from pxr import Usd, UsdShade

    write_usd(
        visual=trimesh.Scene(trimesh.creation.box()),
        collision=trimesh.Scene(trimesh.creation.box()),
        physics=_physics(),
        output_path=tmp_path / "plain.usda",
    )
    stage = Usd.Stage.Open(str(tmp_path / "plain.usda"))
    shader = UsdShade.Shader(stage.GetPrimAtPath("/Asset/Materials/Visual_0/Surface"))
    assert shader.GetInput("metallic").Get() == 1.0


@pytest.mark.parametrize(
    "field,value",
    [
        ("mass_kg", 1e-50),
        ("mass_kg", 1e50),
        ("inertia_kg_m2", (np.eye(3) * 1e-50).tolist()),
        ("inertia_kg_m2", (np.eye(3) * 1e50).tolist()),
    ],
)
def test_usd_rejects_unrepresentable_positive_physics(tmp_path, field, value):
    physics = _physics()
    physics[field] = value
    with pytest.raises(ContractError, match="representable float32"):
        write_usd(
            visual=trimesh.Scene(trimesh.creation.box()),
            collision=trimesh.Scene(trimesh.creation.box()),
            physics=physics,
            output_path=tmp_path / "bad.usda",
        )
    assert not (tmp_path / "bad.usda").exists()


@pytest.mark.parametrize("explicit", [False, True])
def test_glb_normal_interpolation_is_preserved(tmp_path, explicit):
    import io

    from pxr import Usd, UsdGeom

    data = trimesh.Scene(trimesh.creation.box()).export(file_type="glb", include_normals=explicit)
    visual = trimesh.load(io.BytesIO(data), file_type="glb", force="scene", process=False)
    geometry = next(iter(visual.geometry.values()))
    assert ("vertex_normals" in geometry._cache.cache) == explicit
    expected = (
        np.asarray(geometry.vertex_normals).copy()
        if explicit
        else np.repeat(geometry.face_normals, 3, axis=0)
    )
    write_usd(
        visual=visual,
        collision=trimesh.Scene(trimesh.creation.box()),
        physics=_physics(),
        output_path=tmp_path / "normals.usda",
    )
    stage = Usd.Stage.Open(str(tmp_path / "normals.usda"))
    mesh = UsdGeom.Mesh(stage.GetPrimAtPath("/Asset/Visual/Node_0/Mesh"))
    assert mesh.GetNormalsInterpolation() == ("vertex" if explicit else "faceVarying")
    assert np.allclose(mesh.GetNormalsAttr().Get(), expected)

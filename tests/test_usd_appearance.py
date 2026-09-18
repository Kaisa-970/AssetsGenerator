from __future__ import annotations

import numpy as np
import pytest
import trimesh

from assets_generator.usd_writer import write_usd

pytest.importorskip("pxr")


def test_usd_preserves_distinct_materials_with_mirrored_scaled_instances(tmp_path):
    from pxr import Usd, UsdGeom, UsdShade

    scene = trimesh.Scene()
    factors = ([255, 0, 0, 255], [0, 255, 0, 255])
    for index, factor in enumerate(factors):
        mesh = trimesh.creation.box()
        mesh.visual = trimesh.visual.texture.TextureVisuals(
            material=trimesh.visual.material.PBRMaterial(
                baseColorFactor=factor, metallicFactor=0.25, roughnessFactor=0.75
            )
        )
        transform = np.diag([-1.0, 2.0, 3.0, 1.0])
        transform[0, 3] = index * 4.0
        scene.add_geometry(mesh, node_name=f"part_{index}", transform=transform)
    write_usd(
        visual=scene,
        collision=trimesh.Scene(trimesh.creation.box()),
        physics={
            "mass_kg": 1.0,
            "center_of_mass_m": [0, 0, 0],
            "inertia_kg_m2": np.eye(3).tolist(),
            "static_friction": 0.5,
            "dynamic_friction": 0.4,
            "restitution": 0.0,
        },
        output_path=tmp_path / "asset.usda",
    )
    stage = Usd.Stage.Open(str(tmp_path / "asset.usda"))
    for index, factor in enumerate(factors):
        prim = stage.GetPrimAtPath(f"/Asset/Visual/Node_{index}/Mesh")
        material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
        shader = UsdShade.Shader(stage.GetPrimAtPath(str(material.GetPath()) + "/Surface"))
        assert np.allclose(shader.GetInput("diffuseColor").Get(), np.array(factor[:3]) / 255)
        world = np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(prim)).T
        assert np.allclose(world[:3, :3], np.diag([-1.0, 2.0, 3.0]))
        assert world[0, 3] == index * 4.0

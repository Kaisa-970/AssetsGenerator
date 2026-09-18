"""Optional OpenUSD export of a single canonical metric rigid asset."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import sys
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from .contracts import ContractError


def _sdk() -> tuple[Any, ...]:
    try:
        return tuple(
            importlib.import_module(f"pxr.{name}")
            for name in ("Usd", "UsdGeom", "UsdPhysics", "UsdShade", "Sdf", "Gf", "Vt")
        )
    except ImportError as error:
        raise ContractError("USD export requires the optional usd-core package") from error


def sdk_environment_identity() -> dict[str, Any]:
    _sdk()
    distribution = importlib.metadata.distribution("usd-core")
    digest = hashlib.sha256()
    for relative in sorted(distribution.files or [], key=str):
        if relative.suffix == ".pyc" or "__pycache__" in relative.parts:
            continue
        path = Path(str(distribution.locate_file(relative)))
        if path.is_file():
            digest.update(str(relative).encode())
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    executable = Path(sys.executable).absolute()
    return {
        "usd_core_version": distribution.version,
        "installed_content_digest": "sha256:" + digest.hexdigest(),
        "python_executable": str(executable),
        "python_executable_digest": "sha256:" + hashlib.sha256(executable.read_bytes()).hexdigest(),
        "python_prefix": sys.prefix,
        "python_version": sys.version,
        "profile": "openusd-rigid-v1",
        "writer_digest": "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def principal_inertia(tensor: Any) -> tuple[Any, Any]:
    """Ascending moments; projected canonical axes resolve repeated eigenspaces."""
    matrix = np.asarray(tensor, dtype=float)
    if (
        matrix.shape != (3, 3)
        or not np.isfinite(matrix).all()
        or not np.allclose(matrix, matrix.T, atol=0, rtol=1e-12)
    ):
        raise ContractError("inertia must be a finite symmetric 3x3 tensor")
    values, vectors = np.linalg.eigh(matrix)
    if values[0] <= 0 or values[2] > values[0] + values[1] + values[2] * 1e-12:
        raise ContractError("inertia must have positive physical principal moments")
    tolerance = float(values[-1]) * 1e-12
    start = 0
    while start < 3:
        end = start + 1
        while end < 3 and abs(values[end] - values[start]) <= tolerance:
            end += 1
        projector = vectors[:, start:end] @ vectors[:, start:end].T
        basis: list[Any] = []
        for axis in np.eye(3):
            candidate = projector @ axis
            for previous in basis:
                candidate -= previous * np.dot(previous, candidate)
            norm = np.linalg.norm(candidate)
            if norm > 1e-10:
                basis.append(candidate / norm)
            if len(basis) == end - start:
                break
        vectors[:, start:end] = np.column_stack(basis)
        start = end
    if np.linalg.det(vectors) < 0:
        vectors[:, -1] *= -1
    return values, vectors


def _material_data(mesh: trimesh.Trimesh) -> dict[str, Any]:
    visual: Any = mesh.visual
    result: dict[str, Any] = {
        "factor": np.ones(4),
        "metallic": 1.0,
        "roughness": 1.0,
        "double_sided": False,
    }
    if visual.kind in ("vertex", "face"):
        colors = np.asarray(visual.vertex_colors if visual.kind == "vertex" else visual.face_colors)
        if np.any(colors[:, 3] != 255):
            raise ContractError("USD v1 does not support transparent vertex/face colors")
        result.update(
            colors=colors[:, :3] / 255.0,
            interpolation="vertex" if visual.kind == "vertex" else "uniform",
        )
    elif visual.kind == "texture":
        result["metallic"] = 1.0
        material = visual.material
        if not isinstance(material, trimesh.visual.material.PBRMaterial):
            raise ContractError("USD v1 requires PBRMaterial for textured geometry")
        allowed = {
            "baseColorFactor",
            "baseColorTexture",
            "metallicFactor",
            "roughnessFactor",
            "doubleSided",
            "alphaMode",
            "alphaCutoff",
        }
        for name, value in material._data.items():
            if name not in allowed and value is not None:
                raise ContractError(f"USD v1 unsupported PBR material feature: {name}")
        if material.alphaMode not in (None, "OPAQUE") or material.alphaCutoff is not None:
            raise ContractError("USD v1 supports only opaque PBR materials")
        if material.baseColorFactor is not None:
            result["factor"] = np.asarray(material.baseColorFactor, dtype=float) / 255.0
        if result["factor"][3] != 1:
            raise ContractError("USD v1 does not support transparent base color factors")
        for key, attr in (("metallic", "metallicFactor"), ("roughness", "roughnessFactor")):
            value = getattr(material, attr)
            if value is not None:
                result[key] = float(value)
        result["double_sided"] = bool(material.doubleSided)
        texture = material.baseColorTexture
        if texture is not None:
            uv = np.asarray(visual.uv, dtype=float)
            if uv.shape != (len(mesh.vertices), 2) or not np.isfinite(uv).all():
                raise ContractError("base color texture requires finite per-vertex UVs")
            if texture.mode not in ("RGB", "RGBA"):
                raise ContractError("base color texture must use RGB or RGBA encoding")
            result.update(texture=texture, uv=uv)
    elif visual.kind is not None:
        raise ContractError("USD v1 unsupported visual representation")
    return result


def write_usd(
    *, visual: trimesh.Scene, collision: trimesh.Scene, physics: dict[str, Any], output_path: Path
) -> dict[str, Any]:
    Usd, Geom, Physics, Shade, Sdf, Gf, Vt = _sdk()
    if output_path.suffix != ".usda" or output_path.exists():
        raise ContractError("USD output must be a new .usda file")
    moments, axes = principal_inertia(physics["inertia_kg_m2"])
    positive_values = np.asarray([physics["mass_kg"], *moments], dtype=float)
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        encoded_values = positive_values.astype(np.float32)
    if (
        not np.isfinite(encoded_values).all()
        or np.any(encoded_values <= 0)
        or not np.allclose(encoded_values, positive_values, rtol=1e-6, atol=0)
    ):
        raise ContractError(
            "USD mass and principal inertia must be positive representable float32 values"
        )
    normal_data = {
        name: (np.asarray(mesh._cache.cache["vertex_normals"]).copy(), "vertex")
        if "vertex_normals" in mesh._cache.cache
        else (np.repeat(np.asarray(mesh.face_normals), 3, axis=0), "faceVarying")
        for name, mesh in visual.geometry.items()
    }
    rotation = Gf.Matrix3d(*axes.T.reshape(-1).tolist()).ExtractRotation().GetQuat()
    if rotation.GetReal() < 0 or (
        rotation.GetReal() == 0 and next((v for v in rotation.GetImaginary() if v), 1) < 0
    ):
        rotation = -rotation
    # Validate all appearance before creating output files.
    materials = {name: _material_data(mesh) for name, mesh in visual.geometry.items()}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(output_path))
    root = Geom.Xform.Define(stage, "/Asset")
    stage.SetDefaultPrim(root.GetPrim())
    Geom.SetStageUpAxis(stage, Geom.Tokens.z)
    Geom.SetStageMetersPerUnit(stage, 1.0)
    Physics.SetStageKilogramsPerUnit(stage, 1.0)
    Physics.RigidBodyAPI.Apply(root.GetPrim()).CreateRigidBodyEnabledAttr(True)
    mass = Physics.MassAPI.Apply(root.GetPrim())
    mass.CreateMassAttr(float(physics["mass_kg"]))
    mass.CreateCenterOfMassAttr(Gf.Vec3f(*physics["center_of_mass_m"]))
    mass.CreateDiagonalInertiaAttr(Gf.Vec3f(*moments.tolist()))
    mass.CreatePrincipalAxesAttr(Gf.Quatf(rotation))
    physical_material = Shade.Material.Define(stage, "/Asset/Materials/Physics")
    material_api = Physics.MaterialAPI.Apply(physical_material.GetPrim())
    for method, key in (
        ("CreateStaticFrictionAttr", "static_friction"),
        ("CreateDynamicFrictionAttr", "dynamic_friction"),
        ("CreateRestitutionAttr", "restitution"),
    ):
        getattr(material_api, method)(float(physics[key]))
    authored: list[tuple[str, Any, Any, Any, bool]] = []
    texture_paths: list[str] = []
    for scene, category in ((visual, "Visual"), (collision, "Collision")):
        if not scene.graph.nodes_geometry:
            raise ContractError(f"USD {category} geometry is empty")
        Geom.Xform.Define(stage, f"/Asset/{category}")
        prototypes: dict[Any, str] = {}
        for index, node in enumerate(sorted(scene.graph.nodes_geometry)):
            transform, geometry_name = scene.graph[node]
            mesh = scene.geometry[geometry_name]
            vertices, faces = np.asarray(mesh.vertices), np.asarray(mesh.faces)
            if (
                not len(vertices)
                or faces.ndim != 2
                or faces.shape[1] != 3
                or not len(faces)
                or not np.isfinite(vertices).all()
                or not np.isfinite(transform).all()
            ):
                raise ContractError("USD geometry must be finite nonempty triangles")
            node_path = f"/Asset/{category}/Node_{index}"
            xform = Geom.Xform.Define(stage, node_path)
            xform.AddTransformOp().Set(Gf.Matrix4d(*np.asarray(transform).T.reshape(-1).tolist()))
            mesh_path = node_path + "/Mesh"
            usd_mesh = Geom.Mesh.Define(stage, mesh_path)
            if geometry_name in prototypes:
                usd_mesh.GetPrim().GetReferences().AddInternalReference(prototypes[geometry_name])
                usd_mesh.GetPrim().SetInstanceable(True)
            else:
                prototypes[geometry_name] = mesh_path
                usd_mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(vertices.astype(np.float32)))
                usd_mesh.CreateFaceVertexCountsAttr([3] * len(faces))
                usd_mesh.CreateFaceVertexIndicesAttr(faces.reshape(-1).tolist())
                usd_mesh.CreateSubdivisionSchemeAttr(Geom.Tokens.none)
                if category == "Collision":
                    usd_mesh.CreatePurposeAttr(Geom.Tokens.guide)
                    Physics.CollisionAPI.Apply(usd_mesh.GetPrim()).CreateCollisionEnabledAttr(True)
                    Physics.MeshCollisionAPI.Apply(usd_mesh.GetPrim()).CreateApproximationAttr(
                        "convexHull"
                    )
                    Shade.MaterialBindingAPI.Apply(usd_mesh.GetPrim()).Bind(
                        physical_material, materialPurpose="physics"
                    )
                else:
                    data = materials[geometry_name]
                    usd_mesh.CreateDoubleSidedAttr(data["double_sided"])
                    usd_mesh.CreateNormalsAttr(
                        Vt.Vec3fArray.FromNumpy(normal_data[geometry_name][0].astype(np.float32))
                    )
                    usd_mesh.SetNormalsInterpolation(normal_data[geometry_name][1])
                    material = Shade.Material.Define(stage, f"/Asset/Materials/Visual_{index}")
                    shader = Shade.Shader.Define(stage, str(material.GetPath()) + "/Surface")
                    shader.CreateIdAttr("UsdPreviewSurface")
                    diffuse = shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f)
                    diffuse.Set(Gf.Vec3f(*data["factor"][:3]))
                    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(data["metallic"])
                    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(data["roughness"])
                    shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
                    material.CreateSurfaceOutput().ConnectToSource(
                        shader.ConnectableAPI(), "surface"
                    )
                    Shade.MaterialBindingAPI.Apply(usd_mesh.GetPrim()).Bind(material)
                    primvars = Geom.PrimvarsAPI(usd_mesh)
                    if "colors" in data:
                        primvars.CreatePrimvar(
                            "displayColor", Sdf.ValueTypeNames.Color3fArray, data["interpolation"]
                        ).Set(Vt.Vec3fArray.FromNumpy(data["colors"].astype(np.float32)))
                        reader = Shade.Shader.Define(stage, str(material.GetPath()) + "/Color")
                        reader.CreateIdAttr("UsdPrimvarReader_float3")
                        reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("displayColor")
                        reader.CreateOutput("result", Sdf.ValueTypeNames.Float3)
                        diffuse.ConnectToSource(reader.ConnectableAPI(), "result")
                    if "texture" in data:
                        texture_dir = output_path.parent / "textures"
                        texture_dir.mkdir(exist_ok=True)
                        texture_path = texture_dir / f"base_color_{index}.png"
                        if texture_path.exists():
                            raise ContractError("USD texture output already exists")
                        data["texture"].save(texture_path, format="PNG")
                        relative = texture_path.relative_to(output_path.parent).as_posix()
                        texture_paths.append(relative)
                        primvars.CreatePrimvar(
                            "st", Sdf.ValueTypeNames.TexCoord2fArray, "vertex"
                        ).Set(Vt.Vec2fArray.FromNumpy(data["uv"].astype(np.float32)))
                        reader = Shade.Shader.Define(stage, str(material.GetPath()) + "/UV")
                        reader.CreateIdAttr("UsdPrimvarReader_float2")
                        reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
                        reader.CreateOutput("result", Sdf.ValueTypeNames.Float2)
                        texture_shader = Shade.Shader.Define(
                            stage, str(material.GetPath()) + "/Texture"
                        )
                        texture_shader.CreateIdAttr("UsdUVTexture")
                        texture_shader.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
                        texture_shader.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
                        texture_shader.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
                            Sdf.AssetPath(relative)
                        )
                        texture_shader.CreateInput(
                            "sourceColorSpace", Sdf.ValueTypeNames.Token
                        ).Set("sRGB")
                        texture_shader.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set(
                            Gf.Vec4f(*data["factor"])
                        )
                        texture_shader.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
                            reader.ConnectableAPI(), "result"
                        )
                        texture_shader.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
                        diffuse.ConnectToSource(texture_shader.ConnectableAPI(), "rgb")
            authored.append((mesh_path, vertices, faces, transform, category == "Collision"))
    stage.GetRootLayer().Save()
    loaded = Usd.Stage.Open(str(output_path))
    if (
        Geom.GetStageUpAxis(loaded) != "Z"
        or Geom.GetStageMetersPerUnit(loaded) != 1.0
        or Physics.GetStageKilogramsPerUnit(loaded) != 1.0
    ):
        raise ContractError("USD roundtrip spatial metadata mismatch")
    restored_mass = Physics.MassAPI(loaded.GetPrimAtPath("/Asset"))
    restored_moments = np.asarray(restored_mass.GetDiagonalInertiaAttr().Get())
    if np.any(restored_moments <= 0) or not np.allclose(
        restored_moments, moments, rtol=1e-6, atol=0
    ):
        raise ContractError("USD roundtrip principal inertia mismatch")
    restored_rotation = np.asarray(Gf.Matrix3d(restored_mass.GetPrincipalAxesAttr().Get())).T
    restored_tensor = (
        restored_rotation
        @ np.diag(restored_mass.GetDiagonalInertiaAttr().Get())
        @ restored_rotation.T
    )
    if not np.allclose(
        restored_tensor, physics["inertia_kg_m2"], atol=float(moments[-1]) * 2e-6, rtol=2e-6
    ):
        raise ContractError("USD roundtrip inertia mismatch")
    if not np.isclose(
        restored_mass.GetMassAttr().Get(), physics["mass_kg"], rtol=1e-6, atol=0
    ) or not np.allclose(
        restored_mass.GetCenterOfMassAttr().Get(), physics["center_of_mass_m"], rtol=1e-6, atol=1e-7
    ):
        raise ContractError("USD roundtrip mass/center mismatch")
    restored_physics = Physics.MaterialAPI(loaded.GetPrimAtPath("/Asset/Materials/Physics"))
    for method, key in (
        ("GetStaticFrictionAttr", "static_friction"),
        ("GetDynamicFrictionAttr", "dynamic_friction"),
        ("GetRestitutionAttr", "restitution"),
    ):
        if not np.isclose(
            getattr(restored_physics, method)().Get(), physics[key], rtol=1e-6, atol=0
        ):
            raise ContractError("USD roundtrip physics material mismatch")
    if not Physics.RigidBodyAPI(loaded.GetPrimAtPath("/Asset")).GetRigidBodyEnabledAttr().Get():
        raise ContractError("USD roundtrip rigid body disabled")
    for path, vertices, faces, transform, is_collision in authored:
        prim = loaded.GetPrimAtPath(path)
        mesh = Geom.Mesh(prim)
        if not np.allclose(
            mesh.GetPointsAttr().Get(), vertices, rtol=1e-6, atol=1e-7
        ) or not np.array_equal(
            np.asarray(mesh.GetFaceVertexIndicesAttr().Get()).reshape(-1, 3), faces
        ):
            raise ContractError("USD roundtrip geometry mismatch")
        matrix = np.asarray(Geom.XformCache().GetLocalToWorldTransform(prim)).T
        if not np.allclose(matrix, transform, rtol=1e-9, atol=1e-9):
            raise ContractError("USD roundtrip transform mismatch")
        if prim.HasAPI(Physics.CollisionAPI) != is_collision:
            raise ContractError("USD roundtrip collision separation mismatch")
        material, _ = Shade.MaterialBindingAPI(prim).ComputeBoundMaterial(
            materialPurpose="physics" if is_collision else ""
        )
        if not material:
            raise ContractError("USD roundtrip material binding is missing")
        if is_collision:
            if mesh.GetPurposeAttr().Get() != "guide":
                raise ContractError("USD roundtrip collision render purpose mismatch")
            if Physics.MeshCollisionAPI(prim).GetApproximationAttr().Get() != "convexHull":
                raise ContractError("USD roundtrip collision approximation mismatch")
        else:
            node_index = int(path.split("Node_", 1)[1].split("/", 1)[0])
            geometry_name = visual.graph[sorted(visual.graph.nodes_geometry)[node_index]][1]
            expected_material = materials[geometry_name]
            expected_normals, expected_interpolation = normal_data[geometry_name]
            if mesh.GetNormalsInterpolation() != expected_interpolation or not np.allclose(
                mesh.GetNormalsAttr().Get(), expected_normals, rtol=1e-6, atol=1e-7
            ):
                raise ContractError("USD roundtrip normal mismatch")
            surface = Shade.Shader(loaded.GetPrimAtPath(str(material.GetPath()) + "/Surface"))
            for key in ("metallic", "roughness"):
                if not np.isclose(surface.GetInput(key).Get(), expected_material[key], rtol=1e-6):
                    raise ContractError("USD roundtrip PBR factor mismatch")
            primvars = Geom.PrimvarsAPI(prim)
            if "colors" in expected_material and not np.allclose(
                primvars.GetPrimvar("displayColor").Get(), expected_material["colors"], atol=1e-7
            ):
                raise ContractError("USD roundtrip vertex colors mismatch")
            if "texture" in expected_material:
                texture_shader = Shade.Shader(
                    loaded.GetPrimAtPath(str(material.GetPath()) + "/Texture")
                )
                relative = texture_shader.GetInput("file").Get().path
                if any(
                    texture_shader.GetInput(axis).Get() != "repeat" for axis in ("wrapS", "wrapT")
                ):
                    raise ContractError("USD roundtrip texture wrapping mismatch")
                from PIL import Image

                with Image.open(output_path.parent / relative) as restored_image:
                    restored_image.load()
                    if (
                        restored_image.format != "PNG"
                        or restored_image.mode != expected_material["texture"].mode
                        or not np.array_equal(
                            np.asarray(restored_image), np.asarray(expected_material["texture"])
                        )
                    ):
                        raise ContractError("USD roundtrip texture payload mismatch")
                if (
                    relative not in texture_paths
                    or not surface.GetInput("diffuseColor").HasConnectedSource()
                ):
                    raise ContractError("USD roundtrip texture binding mismatch")
                if not np.allclose(
                    texture_shader.GetInput("scale").Get(), expected_material["factor"], atol=1e-7
                ) or not np.allclose(
                    primvars.GetPrimvar("st").Get(), expected_material["uv"], atol=1e-7
                ):
                    raise ContractError("USD roundtrip texture factor/UV mismatch")
            elif "colors" not in expected_material and not np.allclose(
                surface.GetInput("diffuseColor").Get(), expected_material["factor"][:3], atol=1e-7
            ):
                raise ContractError("USD roundtrip base color mismatch")
    for relative in texture_paths:
        if not (output_path.parent / relative).is_file():
            raise ContractError("USD roundtrip texture is missing")
    return {
        "profile": "openusd-rigid-v1",
        "status": "pass",
        "mesh_instances": len(authored),
        "textures": texture_paths,
        "inertia_max_error": float(np.max(np.abs(restored_tensor - physics["inertia_kg_m2"]))),
    }

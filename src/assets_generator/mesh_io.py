"""GLB reading and scene-space vertex access, independent of workflows."""

from __future__ import annotations

import io
from typing import Any

import numpy as np
import trimesh


class OperatorExecutionError(RuntimeError):
    pass


def load_scene(data: bytes) -> trimesh.Scene:
    loaded = trimesh.load(io.BytesIO(data), file_type="glb", force="scene")
    if not isinstance(loaded, trimesh.Scene):
        return trimesh.Scene(loaded)
    return loaded


def scene_vertices(scene: trimesh.Scene) -> np.ndarray[Any, np.dtype[np.float64]]:
    vertices: list[np.ndarray[Any, np.dtype[np.float64]]] = []
    for node_name in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph[node_name]
        geometry = scene.geometry[geometry_name]
        points = trimesh.transform_points(np.asarray(geometry.vertices), transform)
        vertices.append(np.asarray(points, dtype=np.float64))
    if not vertices:
        raise OperatorExecutionError("GLB contains no mesh geometry")
    return np.vstack(vertices)

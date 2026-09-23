"""CPU integration fixture, NOT a real image reconstruction model.

Run with PYTHONPATH=src python examples/shape_model_service_cpu.py
and arguments --directory /tmp/shape-demo --port 8780.
"""

import argparse
from pathlib import Path

import trimesh
from PIL import Image

from assets_generator.models import BackendNativeFrame
from assets_generator.serialization import sha256_bytes
from assets_generator.shape_model_service import ShapeModelService


def infer(image_path, parameters):
    with Image.open(image_path) as image:
        color = image.convert("RGBA").getpixel((image.width // 2, image.height // 2))
    mesh = trimesh.creation.box(extents=[parameters["width"], 1, 1])
    mesh.visual.vertex_colors = [*color[:3], 255]
    return mesh.export(file_type="glb")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8780)
    args = parser.parse_args()
    ShapeModelService(
        service_id="cpu-shape-example",
        display_name="CPU 示例盒子（非真实模型）",
        deployment={
            "model": "test-box-no-weights",
            "code": sha256_bytes(Path(__file__).read_bytes()),
            "trimesh": trimesh.__version__,
        },
        frame=BackendNativeFrame("example-native", "right", "+Y", None, "unknown", "relative_unit"),
        infer=infer,
        directory=args.directory,
        port=args.port,
        parameter_schema={
            "type": "object",
            "properties": {"width": {"type": "number", "minimum": 0.1, "maximum": 4.0}},
        },
        defaults={"width": 1.0},
    ).serve()


if __name__ == "__main__":
    main()

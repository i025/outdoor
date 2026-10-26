"""Legacy bundle command-line bridge to the shared graph Blender executor.

Consumes a JSON bundle prepared by the Python 3.11 application. Geometry and
placement of vegetation are provided by the existing Infinigen implementation.
"""

import argparse
import json
import random
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.dont_write_bytecode = True

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector


def local_path(uri, base):
    parsed = urlparse(str(uri))
    if parsed.scheme and parsed.scheme != "file":
        raise ValueError("Blender worker requires local assets: " + str(uri))
    if parsed.scheme == "file" and parsed.netloc not in ("", "localhost"):
        raise ValueError("Non-local file URI: " + str(uri))
    path = Path(unquote(parsed.path) if parsed.scheme else str(uri)).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def tag(obj, node_id, object_id, generator, config):
    obj["og_node_id"] = str(node_id)
    obj["og_object_id"] = str(object_id)
    obj["og_generator"] = str(generator)
    obj["og_config"] = json.dumps(config, sort_keys=True)


def load_surface(node, asset, reference, bundle_dir):
    with np.load(local_path(asset["uri"], bundle_dir), allow_pickle=False) as data:
        vertices = np.asarray(data["vertices"], dtype=np.float64)
        faces = np.asarray(data["faces"], dtype=np.int64)
        uv = np.asarray(data["uv"], dtype=np.float64)
        colors = np.asarray(data["vertex_colors"], dtype=np.float32)
        weights = np.asarray(data["region_weight"], dtype=np.float64)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
        raise ValueError("Invalid MoGe vertices")
    if faces.ndim != 2 or faces.shape[1] != 3 or len(faces) == 0:
        raise ValueError("MoGe mesh requires triangular faces")
    if faces.min() < 0 or faces.max() >= len(vertices):
        raise ValueError("MoGe face indices are out of bounds")
    if uv.shape != (len(vertices), 2) or not np.isfinite(uv).all():
        raise ValueError("Invalid MoGe UV coordinates")
    if colors.shape != (len(vertices), 3) or not np.isfinite(colors).all():
        raise ValueError("Invalid MoGe vertex colors")
    if weights.shape != (len(vertices),) or not np.isfinite(weights).all():
        raise ValueError("Invalid region weights")
    if np.any((weights < 0) | (weights > 1)):
        raise ValueError("Region weights must be in [0, 1]")

    mesh = bpy.data.meshes.new(node["id"] + ":mesh")
    mesh.from_pydata(vertices.tolist(), [], faces.tolist())
    mesh.update()
    obj = bpy.data.objects.new(node["id"], mesh)
    bpy.context.scene.collection.objects.link(obj)
    tag(obj, node["id"], node["id"] + ":mesh", "external.moge.mesh", node["params"])

    uv_layer = mesh.uv_layers.new(name="ReferenceUV")
    color_layer = mesh.color_attributes.new(name="MoGeVertexColor", type="FLOAT_COLOR", domain="POINT")
    rgba = np.concatenate([np.clip(colors, 0, 1), np.ones((len(colors), 1))], axis=1)
    color_layer.data.foreach_set("color", rgba.ravel())
    # Input UV uses the image's upper-left origin; Blender textures use lower-left.
    loop_vertices = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_vertices)
    loop_uv = uv[loop_vertices].copy()
    loop_uv[:, 1] = 1 - loop_uv[:, 1]
    uv_layer.data.foreach_set("uv", loop_uv.ravel())

    # Selecting only faces fully inside the manual mask excludes boundary slivers.
    allowed = np.all(weights[faces] >= 1 - 1e-6, axis=1)
    attr = mesh.attributes.new("og_ground_allowed", type="BOOLEAN", domain="FACE")
    attr.data.foreach_set("value", allowed)
    if not allowed.any():
        raise ValueError("Manual region contains no fully selected mesh triangles")

    image = bpy.data.images.load(str(local_path(reference["uri"], bundle_dir)), check_existing=True)
    image.pack()
    material = bpy.data.materials.new("MoGeReferenceTexture")
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.interpolation = "Linear"
    material.node_tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    material.node_tree.links.new(texture.outputs["Color"], shader.inputs["Emission"])
    shader.inputs["Emission Strength"].default_value = 0.2
    shader.inputs["Roughness"].default_value = 1.0
    shader.inputs["Specular"].default_value = 0.0
    mesh.materials.append(material)
    return obj, vertices, faces, allowed


def create_camera(camera, render_width=640):
    scene = bpy.context.scene
    width, height = int(camera["width"]), int(camera["height"])
    if width <= 0 or height <= 0:
        raise ValueError("Camera dimensions must be positive")
    render_height = max(1, round(height * render_width / width))
    original_k = np.asarray(camera["intrinsics_pixels"], dtype=float)
    transform = np.asarray(camera["world_from_camera_cv"], dtype=float)
    if original_k.shape != (3, 3) or transform.shape != (4, 4):
        raise ValueError("Invalid camera matrix dimensions")
    if not np.isfinite(original_k).all() or not np.isfinite(transform).all():
        raise ValueError("Camera matrices must be finite")
    if abs(original_k[0, 1]) > 1e-6 or original_k[0, 0] <= 0 or original_k[1, 1] <= 0:
        raise ValueError("Expected positive focal lengths and zero camera skew")
    k = np.diag([render_width / width, render_height / height, 1.0]) @ original_k
    scene.render.resolution_x = render_width
    scene.render.resolution_y = render_height
    scene.render.resolution_percentage = 100
    pixel_ratio = float(k[0, 0] / k[1, 1])
    # Blender clamps both aspect components to >= 1, so normalize the pair.
    scene.render.pixel_aspect_x = max(1.0, 1.0 / pixel_ratio)
    scene.render.pixel_aspect_y = max(1.0, pixel_ratio)
    bpy.ops.object.camera_add()
    obj = bpy.context.object
    obj.name = camera.get("id", "reference_camera")
    obj.data.type = "PERSP"
    obj.data.sensor_fit = "HORIZONTAL"
    obj.data.sensor_width = 36.0
    obj.data.lens = float(k[0, 0] * 36.0 / render_width)
    obj.data.shift_x = float((render_width / 2 - k[0, 2]) / render_width)
    obj.data.shift_y = float((k[1, 2] - render_height / 2) / render_width * pixel_ratio)
    obj.data.clip_start = 0.001
    obj.data.clip_end = 100000.0
    obj.matrix_world = Matrix((transform @ np.diag([1, -1, -1, 1])).tolist())
    tag(obj, obj.name, obj.name + ":camera", "camera.from_calibration", camera)
    scene.camera = obj
    bpy.context.view_layer.update()

    errors = []
    for point in ([0, 0, 1], [0.1, 0.1, 1], [0.2, -0.1, 2]):
        world = transform @ np.array([*point, 1.0])
        ndc = world_to_camera_view(scene, obj, Vector(world[:3]))
        actual = np.array([ndc.x * render_width, (1 - ndc.y) * render_height])
        projected = k @ np.array(point)
        errors.append(float(np.linalg.norm(actual - projected[:2] / projected[2])))
    if max(errors) > 1e-3:
        raise ValueError("Blender camera projection disagrees with K: " + str(errors))
    return obj, k, max(errors)


def export_glb(surface, scatter, out, butil):
    """Realize native generated instances for a self-contained standard GLB."""
    realized = scatter.copy()
    realized.data = scatter.data.copy()
    realized.name = scatter.name + ":export_realized"
    bpy.context.scene.collection.objects.link(realized)
    for modifier in realized.modifiers:
        if modifier.type != "NODES":
            continue
        modifier.node_group = modifier.node_group.copy()
        tree = modifier.node_group
        output = next(n for n in tree.nodes if n.type == "GROUP_OUTPUT" and n.is_active_output)
        geometry = output.inputs["Geometry"]
        upstream = geometry.links[0].from_socket
        tree.links.remove(geometry.links[0])
        realize = tree.nodes.new("GeometryNodeRealizeInstances")
        tree.links.new(upstream, realize.inputs["Geometry"])
        tree.links.new(realize.outputs["Geometry"], geometry)
    try:
        with butil.SelectObjects([realized]):
            bpy.ops.object.convert(target="MESH")
        with butil.SelectObjects([surface, realized]):
            bpy.ops.export_scene.gltf(
                filepath=str(out / "scene.glb"), export_format="GLB",
                use_selection=True, export_apply=True, export_extras=True,
            )
    finally:
        bpy.data.objects.remove(realized, do_unlink=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--infinigen-source", type=Path, required=True)
    argv = sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    # Keep the original command-line contract and geometry/camera helpers, while
    # running every external node through the shared typed Planner registry.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from outdoorgraph.adapters.graph_blender_worker import run_bundle
    run_bundle(args.bundle, args.out, args.infinigen_source)


if __name__ == "__main__":
    main()

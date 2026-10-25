"""External Blender 3.6 worker; deliberately imports no OutdoorGraph package.

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
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    sys.path.insert(0, str(args.infinigen_source.resolve()))
    from infinigen.assets.grassland.grass_tuft import GrassTuftFactory
    from infinigen.core import init
    from infinigen.core.nodes.node_info import Nodes
    from infinigen.core.placement.factory import make_asset_collection
    from infinigen.core.placement.instance_scatter import scatter_instances
    from infinigen.core.util import blender as butil
    from infinigen.core.util.math import FixedSeed

    bundle_path = args.bundle.resolve()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    scene_ir = bundle["scene"]
    if scene_ir.get("world", {}).get("units") != "m" or scene_ir["world"].get("axis") != "z_up":
        raise ValueError("Worker expects metric z-up Scene IR")
    nodes = {n["id"]: n for n in scene_ir["nodes"]}
    assets = {a["id"]: a for a in bundle["assets"]}
    surface_node = nodes["moge_surface"]
    vegetation = nodes["infinigen_grass"]
    params = vegetation["params"]
    if params["surface_node_id"] != surface_node["id"]:
        raise ValueError("Vegetation surface does not match the imported MoGe mesh")
    region = next(r for r in bundle["regions"] if r["id"] == params["region_id"])
    if region.get("source") != "manual":
        raise ValueError("Vegetation requires an explicit manual region")
    if not local_path(region["uri"], bundle_path.parent).is_file():
        raise FileNotFoundError("Manual region asset is missing")
    density = float(params.get("density", 0.002))
    scale = float(params.get("scale", 1.0))
    count = int(params.get("asset_count", 2))
    if not np.isfinite(density) or density <= 0 or not 0 < scale <= 100 or not 1 <= count <= 16:
        raise ValueError("Invalid bounded vegetation density/scale/asset_count")
    seed = int(vegetation.get("seed", 2026))
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    random.seed(seed)
    np.random.seed(seed)
    butil.clear_scene()
    init.configure_render_cycles(
        min_samples=0, num_samples=16, time_limit=0,
        adaptive_threshold=0.1, exposure=1.0, denoise=False,
    )
    init.configure_cycles_devices(use_gpu=False)
    surface, vertices, faces, allowed = load_surface(
        surface_node, assets[surface_node["params"]["asset_id"]],
        assets["reference_image"], bundle_path.parent,
    )
    surface["og_provenance"] = json.dumps(surface_node.get("metadata", {}), sort_keys=True)
    camera, render_k, camera_error = create_camera(bundle["camera"])
    bpy.context.view_layer.update()

    def manual_selection(nw):
        node = nw.new_node(Nodes.NamedAttribute, ["og_ground_allowed"],
                           attrs={"data_type": "BOOLEAN"})
        # This node has several identically named output sockets; the tuple
        # lets NodeWrangler choose the enabled BOOLEAN output.
        return node, "Attribute"

    before = set(bpy.data.objects)
    with FixedSeed(seed):
        factory = GrassTuftFactory(seed)
        collection = make_asset_collection([factory], n=count, verbose=False)
        scatter = scatter_instances(
            base_obj=surface, collection=collection, density=density,
            scale=scale, scale_rand=0.2, scale_rand_axi=0.1,
            normal_fac=0.2, ground_offset=0,
            selection=manual_selection, fov=None, dist_max=None,
        )
    scatter.name = vegetation["id"]
    for index, obj in enumerate(sorted(set(bpy.data.objects) - before, key=lambda o: o.name)):
        object_id = vegetation["id"] + (":scatter" if obj == scatter else ":asset:%04d" % index)
        tag(obj, vegetation["id"], object_id,
            "infinigen.assets.grassland.grass_tuft.GrassTuftFactory", {**params, "seed": seed})
        obj["og_provenance"] = json.dumps(vegetation.get("metadata", {}), sort_keys=True)
    bpy.context.view_layer.update()
    instances = []
    for item in bpy.context.evaluated_depsgraph_get().object_instances:
        if item.is_instance:
            instances.append({
                "id": vegetation["id"] + ":instance:%06d" % len(instances),
                "og_node_id": vegetation["id"],
                "position_xyz": list(item.matrix_world.translation),
                "world_matrix": [list(row) for row in item.matrix_world],
            })
    if not instances:
        raise RuntimeError("Native Infinigen scatter produced no vegetation; inspect region and density")

    scene = bpy.context.scene
    scene["og_scene_id"] = str(scene_ir["id"])
    scene["og_bundle_schema"] = str(bundle.get("schema_version", "0.2"))
    scene["og_provenance_json"] = json.dumps(bundle.get("provenance", {
        node["id"]: node.get("metadata", {}) for node in scene_ir["nodes"]
    }), sort_keys=True)
    text = bpy.data.texts.get("OutdoorGraph.SceneBundle.json") or bpy.data.texts.new("OutdoorGraph.SceneBundle.json")
    text.clear()
    text.write(json.dumps(bundle, indent=2, ensure_ascii=False))
    scene.world.color = (0.2, 0.2, 0.2)
    bpy.ops.object.light_add(type="SUN")
    sun = bpy.context.object
    sun.name = "OutdoorGraphSun"
    sun.rotation_euler = (0.3, -0.4, -0.5)
    sun.data.energy = 2.0
    tag(sun, "lighting", "lighting:sun", "blender.sun", {"energy": 2.0})
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(out / "render.png")
    bpy.ops.render.render(write_still=True)

    # Frame the manually selected local region. Far-away monocular background
    # points can span kilometres and would make foreground grass disappear.
    selected_vertices = vertices[np.unique(faces[allowed].ravel())]
    grass_margin = max(0.25, max(max(obj.dimensions) for obj in collection.objects) * scale * 1.5)
    bounds_min = selected_vertices.min(axis=0) - grass_margin
    bounds_max = selected_vertices.max(axis=0) + grass_margin
    center = (bounds_min + bounds_max) / 2
    extent = max(float(np.max(bounds_max - bounds_min)), 1.0)
    bpy.ops.object.camera_add(location=(center + extent * np.array([0.5, -0.8, 0.6])).tolist())
    overview = bpy.context.object
    overview.name = "overview_camera"
    overview.rotation_euler = (Vector(center) - overview.location).to_track_quat("-Z", "Y").to_euler()
    overview.data.type = "ORTHO"
    overview.data.ortho_scale = extent * 1.35
    overview.data.clip_end = extent * 10 + 1000
    overview_config = {"scope": "local manual region new viewpoint", "extent": extent,
                       "bounds_min": bounds_min.tolist(), "bounds_max": bounds_max.tolist()}
    tag(overview, "overview", "overview:camera", "blender.overview", overview_config)
    scene.camera = overview
    scene.render.filepath = str(out / "overview.png")
    bpy.ops.render.render(write_still=True)
    scene.camera = camera
    scene.render.filepath = str(out / "render.png")
    bpy.ops.wm.save_as_mainfile(filepath=str(out / "scene.blend"))
    export_glb(surface, scatter, out, butil)

    manifest = {
        "scene_id": scene_ir["id"], "device": "CPU", "samples": 16,
        "infinigen_version": __import__("infinigen").__version__,
        "blender_version": bpy.app.version_string, "seed": seed,
        "source_factory": "infinigen.assets.grassland.grass_tuft.GrassTuftFactory",
        "scatter_api": "infinigen.core.placement.instance_scatter.scatter_instances",
        "vegetation_params": params, "source_asset_count": len(collection.objects),
        "surface_vertices": len(vertices), "surface_faces": len(faces),
        "selected_faces": int(allowed.sum()),
        "selection_policy": "FACE BOOLEAN: all triangle vertex region weights equal one",
        "manual_region": region,
        "camera": bundle["camera"], "render_intrinsics_pixels": render_k.tolist(),
        "render_width": scene.render.resolution_x, "render_height": scene.render.resolution_y,
        "overview": overview_config,
        "camera_projection_max_error_pixels": camera_error,
        "objects": [
            {"id": obj["og_object_id"], "node_id": obj["og_node_id"], "name": obj.name,
             "generator": obj["og_generator"], "config": json.loads(obj["og_config"])}
            for obj in bpy.data.objects if "og_object_id" in obj
        ],
        "vegetation_instances": instances, "vegetation_instance_count": len(instances),
        "instance_id_policy": "Stable snapshot IDs for identical seed/configuration; no one-to-one identity guarantee across parameter changes",
        "outputs": {name: str(out / name) for name in
                    ("scene.blend", "scene.glb", "render.png", "overview.png")},
    }
    (out / "objects.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "instances": len(instances), "out": str(out)}))


if __name__ == "__main__":
    main()

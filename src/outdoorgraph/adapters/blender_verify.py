"""Audit a reopened Blender scene and independently imported GLB.

Run in a fresh Blender process that has opened scene.blend. This verifier does
not import OutdoorGraph or Infinigen, change the saved scene, or render images.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.dont_write_bytecode = True

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def require(condition, message):
    if not condition:
        raise ValueError(message)


def local_path(uri, root):
    parsed = urlparse(str(uri))
    require(parsed.scheme in ("", "file"), "Expected a local asset URI")
    require(parsed.netloc in ("", "localhost"), "Expected a local file URI")
    path = Path(unquote(parsed.path) if parsed.scheme else str(uri)).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def decode_image(path, expected_size=None):
    image = bpy.data.images.load(str(path), check_existing=False)
    width, height = map(int, image.size)
    require(width > 0 and height > 0, "Image did not decode: " + str(path))
    if expected_size is not None:
        require((width, height) == tuple(expected_size), "Decoded image dimensions disagree: " + str(path))
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(height, width, 4)
    require(np.isfinite(pixels).all(), "Image has non-finite decoded pixels: " + str(path))
    return image, pixels


def provenance_of(obj, node):
    require("og_provenance" in obj, "Missing saved provenance on " + obj.name)
    actual = json.loads(obj["og_provenance"])
    require(actual == node.get("metadata", {}), "Saved provenance mismatch on " + obj.name)
    return actual


def check_tagged_objects(objects, nodes):
    tagged = [obj for obj in objects if "og_object_id" in obj]
    ids = [obj["og_object_id"] for obj in tagged]
    require(len(ids) == len(set(ids)), "Duplicate saved object IDs")
    require(all(isinstance(value, str) and value for value in ids), "Invalid saved object ID")
    found = {}
    for node_id, node in nodes.items():
        if node["operator"] not in {"external.moge.mesh", "external.infinigen.grass"}:
            continue
        matches = [obj for obj in tagged if obj.get("og_node_id") == node_id]
        require(matches, "Missing saved node objects: " + node_id)
        for obj in matches:
            require(bool(obj.get("og_generator")), "Missing saved generator on " + obj.name)
            expected = dict(nodes[node_id]["params"])
            if nodes[node_id]["operator"] == "external.infinigen.grass":
                expected["seed"] = int(nodes[node_id].get("seed", 2026))
            configuration = json.loads(obj["og_config"])
            unified = {"params": nodes[node_id]["params"], "seed": int(nodes[node_id].get("seed", 0))}
            require(configuration in (expected, unified), "Saved node configuration mismatch: " + obj.name)
            provenance_of(obj, nodes[node_id])
        found[node_id] = matches
    return tagged, found


def audit(bundle_path, out):
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    scene = bpy.context.scene
    nodes = {node["id"]: node for node in bundle["scene"]["nodes"]}
    text = bpy.data.texts.get("OutdoorGraph.SceneBundle.json")
    require(text is not None, "Saved scene has no embedded SceneBundle")
    require(json.loads(text.as_string()) == bundle, "Embedded SceneBundle differs from the input bundle")
    require(scene.get("og_scene_id") == bundle["scene"]["id"], "Saved scene ID differs")
    require(scene.get("og_bundle_schema") == str(bundle.get("schema_version", "0.2")), "Saved bundle schema differs")
    provenance = bundle["provenance"]
    require(json.loads(scene["og_provenance_json"]) == provenance, "Saved scene provenance differs")
    for name in ("moge", "infinigen"):
        require(all(isinstance(provenance[name].get(key), str) and provenance[name][key]
                    for key in ("repo", "commit")), "Missing repository provenance: " + name)
    require(bool(provenance["moge"].get("weights")), "Missing MoGe checkpoint provenance")
    packed_images = sum(image.packed_file is not None for image in bpy.data.images)
    require(packed_images > 0, "Saved scene has no packed image")

    tagged, found = check_tagged_objects(bpy.data.objects, nodes)
    manifest = json.loads((out / "objects.json").read_text(encoding="utf-8"))
    program_ids = set(found)
    require({obj["og_object_id"] for obj in tagged if obj.get("og_node_id") in program_ids}
            == {obj["id"] for obj in manifest["objects"] if obj["node_id"] in program_ids},
            "Reopened object IDs differ from the generation manifest")
    surface_ids = [key for key, node in nodes.items() if node["operator"] == "external.moge.mesh"]
    grass_ids = [key for key, node in nodes.items() if node["operator"] == "external.infinigen.grass"]
    require(len(surface_ids) == 1 and len(grass_ids) == 1, "Reference-view audit requires one surface and one grass program")
    surface_id, grass_id = surface_ids[0], grass_ids[0]
    surface = next((obj for obj in found[surface_id] if obj.type == "MESH"), None)
    scatter = next((obj for obj in found[grass_id] if any(m.type == "NODES" for m in obj.modifiers)), None)
    require(surface is not None and scatter is not None, "Missing reconstructed mesh or native Geometry Nodes scatter")
    require(len(surface.data.vertices) > 0 and len(surface.data.polygons) > 0, "Reopened surface is empty")

    camera_spec = bundle["camera"]
    camera = bpy.data.objects.get(camera_spec["id"])
    require(camera is not None and camera.type == "CAMERA", "Missing calibrated camera")
    require(scene.camera == camera, "Saved active camera is not the calibrated reference camera")
    width, height = scene.render.resolution_x, scene.render.resolution_y
    original_k = np.asarray(camera_spec["intrinsics_pixels"], dtype=float)
    scaled_k = np.diag([width / camera_spec["width"], height / camera_spec["height"], 1]) @ original_k
    transform = np.asarray(camera_spec["world_from_camera_cv"], dtype=float)
    expected_matrix = transform @ np.diag([1, -1, -1, 1])
    camera_matrix_error = float(np.max(np.abs(np.asarray(camera.matrix_world) - expected_matrix)))
    require(camera_matrix_error < 1e-5, "Reopened camera pose differs from calibration")
    projection_errors = []
    for point in ([0, 0, 1], [0.1, 0.1, 1], [0.2, -0.1, 2]):
        world = transform @ np.array([*point, 1.0])
        ndc = world_to_camera_view(scene, camera, Vector(world[:3]))
        actual = np.array([ndc.x * width, (1 - ndc.y) * height])
        expected = scaled_k @ np.array(point)
        projection_errors.append(float(np.linalg.norm(actual - expected[:2] / expected[2])))
    projection_error = max(projection_errors)
    require(projection_error <= 1e-3, "Reopened camera projection disagrees with K")

    bpy.context.view_layer.update()
    positions = np.array([list(item.matrix_world.translation)
                          for item in bpy.context.evaluated_depsgraph_get().object_instances
                          if item.is_instance], dtype=float)
    require(len(positions) > 0, "Reopened native scatter contains no actual instances")
    require(len(positions) == manifest["vegetation_instance_count"], "Reopened scatter count differs from generation")
    require(np.isfinite(positions).all(), "Reopened instance positions are non-finite")
    vertices = [surface.matrix_world @ vertex.co for vertex in surface.data.vertices]
    polygons = [tuple(face.vertices) for face in surface.data.polygons]
    bvh = BVHTree.FromPolygons(vertices, polygons, all_triangles=all(len(p) == 3 for p in polygons))
    distances = []
    for position in positions:
        result = bvh.find_nearest(Vector(position))
        require(result[0] is not None, "Surface BVH could not find contact")
        distances.append(float(result[3]))
    tolerance = 1e-3
    contact_error = max(distances)
    require(contact_error < tolerance, "Actual instance surface-contact error exceeds 1e-3 m: " + str(contact_error))

    region_id = nodes[grass_id]["params"]["region_id"]
    region = next(item for item in bundle["regions"] if item["id"] == region_id)
    require(region["source"] == "manual", "Expected explicit manual region provenance")
    _, region_pixels = decode_image(local_path(region["uri"], bundle_path.parent),
                                    (camera_spec["width"], camera_spec["height"]))
    # Blender pixel arrays start bottom-left. Bundle mask indexing starts top-left.
    mask = region_pixels[::-1, :, :3].max(axis=2) > 0.5
    homogeneous = np.column_stack([positions, np.ones(len(positions))])
    cv_points = (np.linalg.inv(transform) @ homogeneous.T).T[:, :3]
    require(np.all(cv_points[:, 2] > 0), "Vegetation instance lies behind the calibrated camera")
    projected = (original_k @ cv_points.T).T
    pixels = projected[:, :2] / projected[:, 2:3]
    require(np.isfinite(pixels).all(), "Non-finite instance mask projection")
    # Pixel centers are at i+0.5. floor maps continuous coordinates to pixel cells.
    indices = np.floor(pixels).astype(np.int64)
    inside_image = ((indices[:, 0] >= 0) & (indices[:, 0] < mask.shape[1]) &
                    (indices[:, 1] >= 0) & (indices[:, 1] < mask.shape[0]))
    require(inside_image.all(), "Actual vegetation instance projects outside the source image")
    inside_mask = mask[indices[:, 1], indices[:, 0]]
    require(inside_mask.all(), "Actual vegetation instance projects outside the manual mask")

    renders = {}
    for name in ("render.png", "overview.png"):
        _, image_pixels = decode_image(out / name, (width, height))
        rgb = image_pixels[:, :, :3]
        variance = float(np.var(rgb))
        spatial_variance = float(np.var(rgb, axis=(0, 1)).mean())
        require(spatial_variance > 1e-8, "Decoded render is spatially constant: " + name)
        renders[name] = {"width": width, "height": height, "finite": True,
                         "variance": variance, "mean_spatial_channel_variance": spatial_variance,
                         "min": float(rgb.min()), "max": float(rgb.max())}

    result = {
        "status": "passed", "scene_id": scene["og_scene_id"],
        "opened_blend": bpy.data.filepath,
        "bundle_sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        "embedded_bundle_matches": True, "provenance_matches": True,
        "provenance": provenance, "packed_images": packed_images,
        "unique_saved_object_ids": len(tagged),
        "surface_vertices": len(surface.data.vertices), "surface_faces": len(surface.data.polygons),
        "camera_pose_max_error": camera_matrix_error,
        "camera_projection_max_error_pixels": projection_error,
        "actual_vegetation_instances": len(positions),
        "surface_contact_max_error_m": contact_error, "surface_contact_tolerance_m": tolerance,
        "manual_mask_inside_instances": int(inside_mask.sum()), "manual_mask_outside_instances": 0,
        "mask_pixel_mapping": "world->CV->source K; floor pixel coordinates with centers at i+0.5",
        "decoded_renders": renders,
    }
    # Preserve this audit in memory before replacing the loaded scene. The source
    # .blend is never saved or changed by this process.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(out / "scene.glb"))
    imported_meshes = [obj for obj in bpy.data.objects if obj.type == "MESH"]
    imported_faces = sum(len(obj.data.polygons) for obj in imported_meshes)
    require(imported_faces > 0, "Independently imported GLB contains no geometry faces")
    imported_tagged, imported_found = check_tagged_objects(bpy.data.objects, nodes)
    result["glb_import"] = {
        "mesh_objects": len(imported_meshes), "faces": imported_faces,
        "unique_object_ids": len(imported_tagged),
        "node_ids": sorted(imported_found), "extras_provenance_matches": True,
        "sha256": hashlib.sha256((out / "scene.glb").read_bytes()).hexdigest(),
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    out = args.out.resolve()
    report_path = out / "reopen_verification.json"
    try:
        report = audit(args.bundle.resolve(), out)
    except Exception as error:
        report_path.write_text(json.dumps({"status": "failed", "error": str(error)}, indent=2), encoding="utf-8")
        raise
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

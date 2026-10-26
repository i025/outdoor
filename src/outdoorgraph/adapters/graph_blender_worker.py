"""Native Blender operators executed by the same typed OutdoorGraph Planner.

Run in Blender 3.6/Python 3.10. A previous scene is a real resource cache: a
preserved node's collection, objects, source assets and GN graph are untouched.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import importlib
import json
import math
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from outdoorgraph.adapters.graph_backend_data import (
    allowed_region_faces, heightfield_mesh, instance_owner, ribbon_xy,
)
from outdoorgraph.execution import InputSlot, OperatorSpec, Planner, execute, proxy_registry
from outdoorgraph.ir import Scene
from outdoorgraph.operators import building_proxy, distance_to_road, height_at, road_polyline


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")


def require(condition, message):
    if not condition:
        raise ValueError(message)


class GeometryValidationError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__(report.get("reason", report["rule"]))


def geometry_gate(report):
    if not report["passed"]:
        raise GeometryValidationError(report)
    return report


def collections_by_node():
    return {c["og_node_id"]: c for c in bpy.data.collections if c.get("og_graph_node_collection")}


def object_for(node_id, role):
    collection = collections_by_node().get(node_id)
    if collection:
        return next((obj for obj in collection.all_objects if obj.get("og_role") == role), None)
    return None


def remove_node_collection(node_id):
    previous = collections_by_node().get(node_id)
    if previous is None:
        return
    def descendants(collection):
        return [child for c in collection.children for child in [c]+descendants(c)]
    children = descendants(previous)
    for obj in list(previous.all_objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for child in reversed(children):
        bpy.data.collections.remove(child)
    bpy.data.collections.remove(previous)
    for _ in range(3):
        for datablocks in (bpy.data.meshes, bpy.data.curves, bpy.data.materials, bpy.data.node_groups):
            for data in list(datablocks):
                if data.users == 0:
                    datablocks.remove(data)


def tagged(obj, node, role, generator, signature):
    obj["og_node_id"] = node.id
    obj["og_object_id"] = node.id+":"+role
    obj["og_role"] = role
    obj["og_operator"] = node.operator
    obj["og_generator"] = generator
    obj["og_config"] = json.dumps({"params": node.params, "seed": node.seed}, sort_keys=True)
    obj["og_provenance"] = json.dumps(node.metadata, sort_keys=True)
    obj["og_signature"] = signature


def instances_by_node():
    result = {}
    for instance in bpy.context.evaluated_depsgraph_get().object_instances:
        if instance.is_instance:
            owner = instance_owner(instance)
            if owner:
                result.setdefault(owner, []).append({
                    "position_xyz": list(instance.matrix_world.translation),
                    "matrix_world": [list(row) for row in instance.matrix_world],
                })
    for values in result.values():
        values.sort(key=lambda value: tuple(v for row in value["matrix_world"] for v in row))
    for node_id, values in result.items():
        for index, value in enumerate(values):
            value["id"] = node_id+":instance:%06d" % index
    return result


def snapshots():
    instances = instances_by_node()
    result = {}
    for node_id, collection in collections_by_node().items():
        objects = []
        for obj in sorted(collection.all_objects, key=lambda value: value.get("og_object_id", value.name)):
            value = {"id": obj.get("og_object_id"), "name": obj.name, "type": obj.type,
                     "matrix_world": [list(row) for row in obj.matrix_world],
                     "properties": {key: obj[key] for key in obj.keys()},
                     "hide_render": obj.hide_render, "hide_viewport": obj.hide_viewport}
            if obj.type == "MESH":
                value["vertices"] = [list(v.co) for v in obj.data.vertices]
                value["faces"] = [list(p.vertices) for p in obj.data.polygons]
                value["materials"] = [m.name if m else None for m in obj.data.materials]
            value["modifiers"] = [{"name": m.name, "type": m.type,
                                    "node_group": m.node_group.name if m.type == "NODES" and m.node_group else None}
                                   for m in obj.modifiers]
            objects.append(value)
        result[node_id] = {"collection": collection.name,
                           "properties": {key: collection[key] for key in collection.keys()},
                           "objects": objects, "instances": instances.get(node_id, [])}
    return result


def world_mesh(obj):
    return ([obj.matrix_world @ v.co for v in obj.data.vertices],
            [tuple(p.vertices) for p in obj.data.polygons])


def mesh_bvh(obj):
    vertices, faces = world_mesh(obj)
    return BVHTree.FromPolygons(vertices, faces, all_triangles=all(len(face) == 3 for face in faces))


def road_mesh_contact(node_id, artifact, bvh, enforce=True):
    vertices, faces = world_mesh(object_for(node_id, "road"))
    points = list(vertices)
    for face in faces:
        triangle = [vertices[i] for i in face]
        points.append(sum(triangle, Vector())/len(triangle))
        points.extend((p+q)/2 for p, q in zip(triangle, triangle[1:]+triangle[:1]))
    error = max(bvh.find_nearest(Vector(p))[3] for p in points)
    tolerance = artifact.get("geometry_contact_tolerance_m", .02)
    report = {"node_id": node_id, "rule": "actual_road_mesh_contact", "passed": error <= tolerance,
            "sample_count": len(points), "max_error_m": error, "tolerance_m": tolerance,
            "reason": "Dense actual road mesh contact is within tolerance" if error <= tolerance else "Dense actual road mesh contact exceeds tolerance",
            "centerline_samples": len(artifact.get("sampled_points", artifact["points"]))}
    return geometry_gate(report) if enforce else report


def surface_z(obj, x, y):
    coords, _ = world_mesh(obj)
    top = max(v.z for v in coords)+max(10, max(v.z for v in coords)-min(v.z for v in coords))
    hit = mesh_bvh(obj).ray_cast(Vector((x, y, top)), Vector((0, 0, -1)))
    require(hit[0] is not None, "Requested road/object point lies outside its terrain mesh")
    return float(hit[0].z)


def material(name, color):
    result = bpy.data.materials.new(name)
    result.diffuse_color = (*color, 1)
    result.use_nodes = True
    bsdf = result.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = .9
    return result


class BlenderSession:
    def __init__(self, request, out, infinigen_source=None, request_path=None):
        self.request, self.out = request, Path(out).resolve()
        self.request_path = Path(request_path).resolve() if request_path else self.out/"request.json"
        self.nodes = {node["id"]: node for node in request["scene"]["nodes"]}
        self.out.mkdir(parents=True, exist_ok=True)
        previous = request.get("previous_blend")
        self.previous_loaded = bool(previous and Path(previous).is_file())
        if self.previous_loaded:
            bpy.ops.wm.open_mainfile(filepath=str(Path(previous).resolve()))
        else:
            bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.context.view_layer.update()
        self.before = snapshots()
        self.removed_ids = sorted(set(collections_by_node())-set(self.nodes))
        for node_id in self.removed_ids:
            remove_node_collection(node_id)
        self.active = {}
        self.artifacts = deepcopy(request.get("previous_artifacts", {}))
        self.infinigen_source = str(Path(infinigen_source).resolve()) if infinigen_source else None
        self.infinigen_commit = None
        if self.infinigen_source:
            sys.path.insert(0, self.infinigen_source)
            self.infinigen_commit = subprocess.check_output(
                ["git", "-C", self.infinigen_source, "rev-parse", "HEAD"], text=True).strip()

    def begin(self, node, context):
        remove_node_collection(node.id)
        collection = bpy.data.collections.new("og:"+node.id)
        bpy.context.scene.collection.children.link(collection)
        collection["og_graph_node_collection"] = True
        collection["og_node_id"] = node.id
        collection["og_signature"] = context["signature"]
        self.active[node.id] = collection
        return collection

    def adopt(self, obj, node, role, context, generator=None):
        collection = self.active[node.id]
        for old in list(obj.users_collection):
            old.objects.unlink(obj)
        collection.objects.link(obj)
        obj.name = "og:"+node.id+":"+role
        tagged(obj, node, role, generator or node.operator, context["signature"])
        return obj

    def mesh(self, node, role, vertices, faces, context, color=None, hidden=False):
        data = bpy.data.meshes.new("og:"+node.id+":"+role)
        data.from_pydata(vertices, [], faces)
        data.update()
        obj = bpy.data.objects.new(data.name, data)
        self.active[node.id].objects.link(obj)
        tagged(obj, node, role, node.operator, context["signature"])
        obj.hide_render = hidden
        if color is not None:
            data.materials.append(material(data.name+":material", color))
        return obj

    def valid_cache(self, artifact, context):
        collection = collections_by_node().get(context["node_id"])
        return bool(collection and collection.get("og_signature") == context["signature"]
                    and collection.all_objects and artifact.get("backend_node_id") == context["node_id"]
                    and artifact.get("source_commit") == self.infinigen_commit
                    and sorted(obj.get("og_object_id", "") for obj in collection.all_objects)
                        == artifact.get("expected_object_ids"))

    def finish_artifact(self, node, artifact, context):
        artifact.update(backend_node_id=node.id, input_bindings=context["input_bindings"])
        artifact["source_commit"] = self.infinigen_commit
        artifact["expected_object_ids"] = sorted(obj.get("og_object_id", "") for obj in self.active[node.id].all_objects)
        self.artifacts[node.id] = artifact
        bpy.context.view_layer.update()
        return artifact

    def terrain(self, node, inputs, context):
        self.begin(node, context)
        artifact = deepcopy(inputs["heightfield"])
        artifact.pop("_execution", None)
        artifact["bounds_xy"] = list(node.bounds_xy)
        spacing = float(node.params.get("grid_spacing_m", .5))
        vertices, faces = heightfield_mesh(artifact, spacing, height_at)
        self.mesh(node, "terrain", vertices, faces, context, color=node.params.get("color", (.20, .30, .10))[:3])
        artifact["mesh_spacing_m"] = spacing
        return self.finish_artifact(node, artifact, context)

    def road(self, node, inputs, context):
        self.begin(node, context)
        artifact = road_polyline(node, {"terrain": inputs["terrain"]})
        terrain_id = context["input_bindings"]["terrain"][0]
        terrain = object_for(terrain_id, "terrain")
        require(terrain is not None, "Road needs an actual registered terrain mesh")
        bvh = mesh_bvh(terrain)
        top = max(v.z for v in world_mesh(terrain)[0])+100
        offset = float(node.params.get("height_offset_m", 0))
        def z(x, y):
            hit = bvh.ray_cast(Vector((x, y, top)), Vector((0, 0, -1)))
            require(hit[0] is not None, "Road strip extends outside its terrain")
            return float(hit[0].z)+offset
        for key in ("points", "sampled_points"):
            if key in artifact:
                artifact[key] = [[p[0], p[1], z(p[0], p[1])] for p in artifact[key]]
        dense = artifact.get("sampled_points", artifact["points"])
        xy, faces = ribbon_xy(dense, artifact["width_m"], float(node.params.get("cross_spacing_m", .25)))
        vertices = [[x, y, z(x, y)] for x, y in xy]
        self.mesh(node, "road", vertices, faces, context, color=node.params.get("color", (.18, .16, .12))[:3])
        artifact["surface_node_id"] = terrain_id
        contact = next((c for c in self.request["scene"].get("constraints", [])
                        if c["rule"] == "road_contact" and node.id in c["node_ids"]), None)
        artifact["geometry_contact_tolerance_m"] = (contact.get("params", {}).get("tolerance_m", .02)
                                                    if contact else .02)
        return self.finish_artifact(node, artifact, context)

    def object(self, node, inputs, context):
        self.begin(node, context)
        artifact = building_proxy(node, {"terrain": inputs["terrain"]})
        terrain_id = context["input_bindings"]["terrain"][0]
        x, y, _ = artifact["position_xyz"]
        artifact["position_xyz"][2] = surface_z(object_for(terrain_id, "terrain"), x, y)
        size = artifact["size_m"]
        require(len(size) == 3 and all(float(v) > 0 for v in size), "Object needs three positive dimensions")
        bpy.ops.mesh.primitive_cube_add(size=1)
        obj = self.adopt(bpy.context.object, node, "object", context)
        obj.scale = size
        obj.location = (x, y, artifact["position_xyz"][2]+size[2]/2)
        obj.data.materials.append(material(obj.name+":material", node.params.get("color", (.55, .15, .08))[:3]))
        artifact["surface_node_id"] = terrain_id
        return self.finish_artifact(node, artifact, context)

    def external_mesh(self, node, inputs, context):
        from outdoorgraph.adapters.blender_worker import load_surface
        self.begin(node, context)
        bundle = self.request["bundle"]
        assets = {a["id"]: a for a in bundle["assets"]}
        image_id = node.params.get("source_image_asset_id", "reference_image")
        obj, vertices, faces, allowed = load_surface(self.nodes[node.id], assets[node.params["asset_id"]],
                                                    assets[image_id], Path(self.request["bundle_path"]).parent)
        self.adopt(obj, node, "terrain", context, "external.moge.mesh")
        artifact = {"kind": "terrain", "params": {}, "bounds_xy": list(node.bounds_xy),
                    "external_mesh": True}
        return self.finish_artifact(node, artifact, context)

    def grass(self, node, inputs, context):
        from infinigen.assets.grassland.grass_tuft import GrassTuftFactory
        from infinigen.core.nodes.node_info import Nodes
        from infinigen.core.placement.factory import make_asset_collection
        from infinigen.core.placement.instance_scatter import scatter_instances
        from infinigen.core.util.math import FixedSeed
        self.begin(node, context)
        terrain_id = context["input_bindings"]["terrain"][0]
        terrain = object_for(terrain_id, "terrain")
        require(terrain is not None, "Native grass needs a registered terrain object")
        vertices = [list(v) for v in world_mesh(terrain)[0]]
        faces = [list(f) for f in world_mesh(terrain)[1]]
        inherited_attr = terrain.data.attributes.get("og_ground_allowed")
        inherited = [item.value for item in inherited_attr.data] if inherited_attr else None
        roads = inputs.get("roads", [])
        buffer = float(node.params.get("road_buffer_m", 1))
        allowed = allowed_region_faces(vertices, faces, node.bounds_xy, roads, buffer,
                                       distance_to_road, inherited)
        require(any(allowed), "Grass region contains no safely allowed terrain faces")
        support = self.mesh(node, "support", vertices, faces, context, hidden=True)
        attr = support.data.attributes.new("og_grass_allowed", type="BOOLEAN", domain="FACE")
        attr.data.foreach_set("value", allowed)
        count = int(node.params.get("asset_count", 2))
        density = float(node.params.get("density", .5))
        scale = float(node.params.get("scale", 2))
        require(1 <= count <= 8 and math.isfinite(density) and 0 < density <= 100
                and math.isfinite(scale) and 0 < scale <= 20, "Invalid bounded native grass parameters")
        def selection(nw):
            return nw.new_node(Nodes.NamedAttribute, ["og_grass_allowed"], attrs={"data_type": "BOOLEAN"}), "Attribute"
        existing_objects = set(bpy.data.objects)
        with FixedSeed(node.seed):
            factory = GrassTuftFactory(node.seed)
            assets = make_asset_collection([factory], n=count, name="og:"+node.id, verbose=False)
            scatter = scatter_instances(base_obj=support, collection=assets, density=density,
                                        scale=scale, scale_rand=.2, scale_rand_axi=.1,
                                        normal_fac=.2, ground_offset=0, selection=selection,
                                        fov=None, dist_max=None)
        # Infinigen names every new scatter group "geo_instance_scatter".
        # Blender may then rename an existing group that owns that base name.
        # Give only the newly created node's group a private name immediately,
        # so a later independent scatter cannot alter preserved metadata.
        for modifier in scatter.modifiers:
            if modifier.type == "NODES" and modifier.node_group:
                group = modifier.node_group
                base = ("og:"+node.id+":scatter_nodes").encode("utf-8")[:55].decode("utf-8", "ignore")
                candidate, index = base, 1
                while bpy.data.node_groups.get(candidate) not in (None, group):
                    candidate = base+"."+str(index)
                    index += 1
                group.name = candidate
        # Source assets must stay in their hidden collection; link that private
        # collection beneath the node, never expose originals in the world.
        for parent in list(bpy.data.collections):
            if assets.name in parent.children:
                parent.children.unlink(assets)
        if assets.name in bpy.context.scene.collection.children:
            bpy.context.scene.collection.children.unlink(assets)
        self.active[node.id].children.link(assets)
        for index, obj in enumerate(sorted(set(bpy.data.objects)-existing_objects, key=lambda value: value.name)):
            role = "scatter" if obj == scatter else "asset:%03d" % index
            if obj == scatter:
                self.adopt(obj, node, role, context, "infinigen.assets.grassland.grass_tuft.GrassTuftFactory")
            else:
                tagged(obj, node, role, "infinigen.assets.grassland.grass_tuft.GrassTuftFactory", context["signature"])
        bpy.context.view_layer.update()
        actual = instances_by_node().get(node.id, [])
        require(actual, "Native scatter generated zero instances")
        artifact = {"kind": "vegetation", "positions": [i["position_xyz"] for i in actual],
                    "requested_count": int(node.params.get("target_instances", len(actual))),
                    "surface_node_id": terrain_id, "region_bounds_xy": list(node.bounds_xy),
                    "road_buffer_m": buffer, "native_density": density, "native_asset_count": count,
                    "native_factory": "infinigen.assets.grassland.grass_tuft.GrassTuftFactory",
                    "source_commit": self.infinigen_commit, "allowed_faces": sum(allowed)}
        return self.finish_artifact(node, artifact, context)

    def check(self, node, artifact, inputs, context):
        if artifact.get("kind") == "terrain":
            obj = object_for(node.id, "terrain")
            require(obj is not None and len(obj.data.polygons) > 0, "Terrain has no actual faces")
        else:
            terrain = object_for(artifact["surface_node_id"], "terrain")
            require(terrain is not None, "Missing actual support terrain")
            bvh = mesh_bvh(terrain)
            if artifact["kind"] == "vegetation":
                points = [i["position_xyz"] for i in instances_by_node().get(node.id, [])]
                require(points, "Missing native GN instances")
                for p in points:
                    require(bvh.find_nearest(Vector(p))[3] < .02, "Native instance is off its actual terrain")
                    for road in inputs.get("roads", []):
                        require(distance_to_road(p[0], p[1], road)+1e-7 >= road["width_m"]/2+artifact["road_buffer_m"],
                                "Native instance violates actual road clearance")
            elif artifact["kind"] == "building":
                require(bvh.find_nearest(Vector(artifact["position_xyz"]))[3] < .02, "Object is off its actual terrain")
            elif artifact["kind"] == "road":
                require(object_for(node.id, "road") is not None, "Road has no actual mesh")
                return road_mesh_contact(node.id, artifact, bvh, enforce=False)
        return True


def backend_registry(session):
    registry = proxy_registry()
    terrain = InputSlot(("terrain",))
    roads = InputSlot(("road",), required=False, multiple=True)
    specs = [
        ("blender.terrain", session.terrain, {"heightfield": terrain}, ("terrain",)),
        ("blender.road", session.road, {"terrain": terrain}, ("road",)),
        ("blender.grass", session.grass, {"terrain": terrain, "roads": roads}, ("vegetation",)),
        ("blender.object", session.object, {"terrain": terrain}, ("building",)),
        ("external.moge.mesh", session.external_mesh, {}, ("terrain",)),
        ("external.infinigen.grass", session.grass, {"terrain": terrain, "roads": roads}, ("vegetation",)),
    ]
    for name, run, slots, outputs in specs:
        registry.register(OperatorSpec(name, run, slots, outputs, backend="blender",
                                       version="2:"+bpy.app.version_string+":"+str(session.infinigen_commit),
                                       postconditions=(session.check,), cache_validator=session.valid_cache))
    return registry


def geometry_validation(artifacts, scene=None):
    checks = []
    instances = instances_by_node()
    for node_id, collection in collections_by_node().items():
        artifact = artifacts[node_id]
        if artifact["kind"] == "terrain":
            continue
        terrain = object_for(artifact["surface_node_id"], "terrain")
        bvh = mesh_bvh(terrain)
        kind = artifact["kind"]
        if kind == "road":
            checks.append(road_mesh_contact(node_id, artifact, bvh))
        elif kind == "vegetation":
            points = [item["position_xyz"] for item in instances.get(node_id, [])]
            require(points, "No actual grass instances: "+node_id)
            error = max(bvh.find_nearest(Vector(p))[3] for p in points)
            require(error <= .02, "Actual grass contact failed: "+node_id)
            bounds = artifact["region_bounds_xy"]
            require(all(bounds[0]-1e-6 <= p[0] <= bounds[2]+1e-6 and bounds[1]-1e-6 <= p[1] <= bounds[3]+1e-6 for p in points),
                    "Actual grass region failed: "+node_id)
            checks.append({"node_id": node_id, "rule": "actual_native_grass_contact", "passed": True,
                           "instances": len(points), "max_error_m": error, "tolerance_m": .02})
            for road_id in artifact.get("input_bindings", {}).get("roads", []):
                road = artifacts[road_id]
                actual = min(distance_to_road(p[0], p[1], road) for p in points)
                limit = road["width_m"]/2+artifact["road_buffer_m"]
                require(actual+1e-7 >= limit, "Actual grass/road clearance failed: "+node_id)
                checks.append({"node_id": node_id, "road_id": road_id, "rule": "actual_grass_road_clearance",
                               "passed": True, "minimum_m": actual, "required_m": limit})
        elif kind == "building":
            obj = object_for(node_id, "object")
            bottom = obj.matrix_world @ Vector((0, 0, -.5))
            error = bvh.find_nearest(bottom)[3]
            require(error <= .02, "Actual object bottom contact failed: "+node_id)
            checks.append({"node_id": node_id, "rule": "actual_object_bottom_contact", "passed": True,
                           "max_error_m": error, "tolerance_m": .02})
    for constraint in (scene or {}).get("constraints", []):
        if constraint["rule"] != "terrain_continuity":
            continue
        left_id, right_id = constraint["node_ids"]
        left, right = artifacts[left_id]["bounds_xy"], artifacts[right_id]["bounds_xy"]
        if abs(left[2]-right[0]) < 1e-8 or abs(right[2]-left[0]) < 1e-8:
            axis, boundary = 0, left[2] if abs(left[2]-right[0]) < 1e-8 else right[2]
        elif abs(left[3]-right[1]) < 1e-8 or abs(right[3]-left[1]) < 1e-8:
            axis, boundary = 1, left[3] if abs(left[3]-right[1]) < 1e-8 else right[3]
        else:
            raise ValueError("Actual terrain continuity requires a shared mesh edge")
        edges = []
        for node_id in (left_id, right_id):
            points = world_mesh(object_for(node_id, "terrain"))[0]
            edge = sorted((float(p[1-axis]), float(p.z)) for p in points if abs(p[axis]-boundary) < 1e-5)
            require(len(edge) >= 2, "Actual terrain mesh has no shared boundary vertices")
            edges.append(edge)
        lo, hi = max(edge[0][0] for edge in edges), min(edge[-1][0] for edge in edges)
        require(hi > lo, "Actual terrain edges do not overlap")
        samples = sorted(set(p[0] for edge in edges for p in edge if lo <= p[0] <= hi))
        samples += [(a+b)/2 for a, b in zip(samples, samples[1:])]
        values = [np.interp(samples, [p[0] for p in edge], [p[1] for p in edge]) for edge in edges]
        error = float(np.max(np.abs(values[0]-values[1])))
        tolerance = float(constraint.get("params", {}).get("tolerance_m", .001))
        checks.append(geometry_gate({"node_ids": [left_id, right_id], "rule": "actual_terrain_continuity",
                       "passed": error <= tolerance, "sample_count": len(samples), "max_error_m": error,
                       "tolerance_m": tolerance, "reason": "Actual shared mesh boundary interpolation must satisfy terrain continuity"}))
    return checks


def fail_final_gate(result, request, error):
    backend_ids = {node_id for node_id, artifact in result.artifacts.items() if artifact.get("backend_node_id")}
    report = getattr(error, "report", {"rule": "actual_backend_geometry", "passed": False,
                                        "reason": str(error), "node_ids": sorted(backend_ids)})
    affected = set(report.get("node_ids", [report["node_id"]] if report.get("node_id") else backend_ids))
    while True:
        expanded = affected | {edge.target_id for edge in result.scene.valid_edges() if edge.source_id in affected}
        if expanded == affected:
            break
        affected = expanded
    result.status = "failed"
    result.failures.append({"phase": "backend_geometry", "reason": str(error),
                            "error_type": type(error).__name__, "check_evidence": report})
    check_id = "backend:"+report["rule"]
    result.constraint_reports.append({"id": check_id, "hard": True,
                                       "nodes": sorted(affected), **report})
    for node_id in affected:
        if node_id in result.artifacts:
            result.artifacts[node_id]["_execution"]["validation_status"] = "failed"
            result.artifacts[node_id]["_execution"].setdefault("constraint_check_ids", []).append(check_id)
    for node_report in result.node_reports:
        if node_report.get("node_id") in affected:
            node_report.update(status="failed", phase="backend_geometry", check_evidence=report)
    for dependency in result.dependency_reports:
        if (dependency["source_id"] in affected or dependency["target_id"] in affected):
            if dependency["status"] in ("proposed", "rejected", "contract_rejected"):
                continue
            dependency.update(status="validated", lifecycle="validated", geometry_status="failed",
                              reason="Actual Blender geometry gate failed", check_evidence=report)
            result.scene.dependencies[dependency["index"]].status = "validated"
    return report


def setup_render(session):
    from infinigen.core import init
    scene = bpy.context.scene
    config = session.request.get("render", {})
    init.configure_render_cycles(min_samples=0, num_samples=int(config.get("samples", 16)),
                                 time_limit=0, adaptive_threshold=.1, exposure=1, denoise=False)
    init.configure_cycles_devices(use_gpu=False)
    scene.render.resolution_x = int(config.get("width", 640))
    scene.render.resolution_y = int(config.get("height", 480))
    scene.render.resolution_percentage = 100
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(session.out/"render.png")
    helper = bpy.data.collections.get("OGPresentation")
    if helper:
        for obj in list(helper.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(helper)
    helper = bpy.data.collections.new("OGPresentation")
    scene.collection.children.link(helper)
    def move(obj):
        for old in list(obj.users_collection):
            old.objects.unlink(obj)
        helper.objects.link(obj)
    bundle = session.request.get("bundle")
    if bundle:
        from outdoorgraph.adapters.blender_worker import create_camera
        camera, k, error = create_camera(bundle["camera"], scene.render.resolution_x)
        session.camera_info = {"camera": bundle["camera"], "render_intrinsics_pixels": k.tolist(),
                               "camera_projection_max_error_pixels": error}
        move(camera)
    else:
        points = [v for c in collections_by_node().values() for obj in c.all_objects
                  if obj.get("og_role") == "terrain" for v in world_mesh(obj)[0]]
        require(points, "No terrain geometry to frame")
        minimum, maximum = np.min(points, axis=0), np.max(points, axis=0)
        center = (minimum+maximum)/2
        extent = max(maximum-minimum)
        position = config.get("camera_location", (center+extent*np.array([.2, -1.0, 1.05])).tolist())
        target = config.get("camera_target", center.tolist())
        bpy.ops.object.camera_add(location=position)
        camera = bpy.context.object
        camera.name = "GraphCamera"
        camera.rotation_euler = (Vector(target)-camera.location).to_track_quat("-Z", "Y").to_euler()
        camera.data.type = "ORTHO"
        camera.data.ortho_scale = float(config.get("ortho_scale", extent*1.18))
        camera.data.clip_end = max(1000, extent*10)
        scene.camera = camera
        move(camera)
        session.camera_info = {}
    bpy.ops.object.light_add(type="SUN")
    sun = bpy.context.object
    sun.rotation_euler = (.2, -.4, -.3)
    sun.data.energy = 3
    move(sun)
    if scene.world is None:
        scene.world = bpy.data.worlds.new("GraphWorld")
    scene.world.color = (.18, .18, .18)


def export_graph_glb(out):
    from infinigen.core.util import blender as butil
    visible = []
    temporary = []
    for collection in collections_by_node().values():
        for obj in list(collection.all_objects):
            role = obj.get("og_role")
            if role in ("terrain", "road", "object"):
                visible.append(obj)
            elif role == "scatter":
                realized = obj.copy()
                realized.data = obj.data.copy()
                bpy.context.scene.collection.objects.link(realized)
                for modifier in realized.modifiers:
                    if modifier.type == "NODES":
                        modifier.node_group = modifier.node_group.copy()
                        tree = modifier.node_group
                        output = next(n for n in tree.nodes if n.type == "GROUP_OUTPUT" and n.is_active_output)
                        socket = output.inputs["Geometry"]
                        upstream = socket.links[0].from_socket
                        tree.links.remove(socket.links[0])
                        realize = tree.nodes.new("GeometryNodeRealizeInstances")
                        tree.links.new(upstream, realize.inputs["Geometry"])
                        tree.links.new(realize.outputs["Geometry"], socket)
                with butil.SelectObjects([realized]):
                    bpy.ops.object.convert(target="MESH")
                visible.append(realized)
                temporary.append(realized)
    try:
        with butil.SelectObjects(visible):
            bpy.ops.export_scene.gltf(filepath=str(Path(out)/"scene.glb"), export_format="GLB",
                                     use_selection=True, export_apply=True, export_extras=True)
    finally:
        for obj in temporary:
            bpy.data.objects.remove(obj, do_unlink=True)


def run_request(request, out, infinigen_source, request_path=None):
    session = BlenderSession(request, out, infinigen_source, request_path)
    registry = backend_registry(session)
    scene = Scene.from_dict(request["scene"])
    write_json(session.out/"request.json", request)
    plan = Planner(scene, registry).plan()
    write_json(session.out/"plan.json", plan.to_dict())
    result = execute(scene, previous=request.get("previous_artifacts"), registry=registry,
                     context={"blender_session": session})
    gate_error = None
    checks = []
    current = snapshots()
    preserved = [node_id for node_id in result.preserved_ids if node_id in current]
    if result.passed:
        try:
            checks = geometry_validation(result.artifacts, request["scene"])
            for node_id in preserved:
                geometry_gate({"node_id": node_id, "rule": "preserved_node_unchanged",
                               "passed": session.before.get(node_id) == current[node_id],
                               "reason": "Preserved collection geometry, instances and custom properties must match"})
        except Exception as error:
            gate_error = error
            checks.append(fail_final_gate(result, request, error))
    state = result.to_dict()
    write_json(session.out/"state.json", state)
    write_json(session.out/"scene_ir.json", result.scene.to_dict() if result.scene else scene.to_dict())
    if not result.passed:
        write_json(session.out/"validation.json", {"status": "failed", "passed": False,
                   "checks": checks, "failures": result.failures})
    require(result.passed, "Unified graph execution failed: "+json.dumps(result.failures))
    setup_render(session)
    scene_bpy = bpy.context.scene
    scene_bpy["og_scene_id"] = scene.id
    scene_bpy["og_execution_backend"] = "unified Planner native Blender operators"
    for name, value in (("OutdoorGraph.GraphRequest.json", request), ("OutdoorGraph.Execution.json", state)):
        text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
        text.clear()
        text.write(json.dumps(value, ensure_ascii=False))
    bundle = request.get("bundle")
    if bundle:
        text = bpy.data.texts.get("OutdoorGraph.SceneBundle.json") or bpy.data.texts.new("OutdoorGraph.SceneBundle.json")
        text.clear()
        text.write(json.dumps(bundle, ensure_ascii=False))
        scene_bpy["og_bundle_schema"] = str(bundle.get("schema_version", "0.2"))
        scene_bpy["og_provenance_json"] = json.dumps(bundle.get("provenance", {}), sort_keys=True)
    bpy.ops.render.render(write_still=True)
    if bundle:
        # Preserve the existing integration output names; this view is explicitly
        # local to the native grass support, not the full distant point cloud.
        support_points = [p for a in result.artifacts.values() if a["kind"] == "vegetation" for p in a["positions"]]
        low, high = np.min(support_points, axis=0), np.max(support_points, axis=0)
        center, extent = (low+high)/2, max(1, float(np.max(high-low)))
        camera = scene_bpy.camera
        bpy.ops.object.camera_add(location=(center+extent*np.array([.5, -.8, .6])).tolist())
        overview = bpy.context.object
        overview.rotation_euler = (Vector(center)-overview.location).to_track_quat("-Z", "Y").to_euler()
        overview.data.type, overview.data.ortho_scale = "ORTHO", extent*1.6
        overview.data.clip_end = max(1000, extent*10)
        scene_bpy.camera = overview
        scene_bpy.render.filepath = str(session.out/"overview.png")
        bpy.ops.render.render(write_still=True)
        scene_bpy.camera = camera
        scene_bpy.render.filepath = str(session.out/"render.png")
    bpy.ops.wm.save_as_mainfile(filepath=str(session.out/"scene.blend"))
    export_graph_glb(session.out)
    flat_objects = [{"id": obj.get("og_object_id"), "node_id": obj.get("og_node_id"),
                     "name": obj.name, "generator": obj.get("og_generator"),
                     "config": json.loads(obj.get("og_config", "{}"))}
                    for obj in bpy.data.objects if obj.get("og_object_id")]
    flat_instances = [{"id": node_id+":instance:%06d" % i, "og_node_id": node_id, **value}
                      for node_id, value_list in instances_by_node().items() for i, value in enumerate(value_list)]
    manifest = {"scene_id": scene.id, "nodes": current, "previous_nodes": session.before,
                "rebuilt_ids": result.rebuilt_ids, "preserved_ids": result.preserved_ids,
                "objects": flat_objects, "vegetation_instances": flat_instances,
                "vegetation_instance_count": len(flat_instances), "device": "CPU",
                "samples": int(request.get("render", {}).get("samples", 16)),
                "render_width": scene_bpy.render.resolution_x, "render_height": scene_bpy.render.resolution_y,
                "blender_version": bpy.app.version_string,
                "infinigen_version": importlib.import_module("infinigen").__version__,
                "source_factory": "infinigen.assets.grassland.grass_tuft.GrassTuftFactory",
                "scatter_api": "infinigen.core.placement.instance_scatter.scatter_instances",
                "source_commit": session.infinigen_commit,
                "preserved_backend_ids": preserved, **session.camera_info}
    manifest["removed_backend_ids"] = session.removed_ids
    terrain_objects = [object_for(node_id, "terrain") for node_id, artifact in result.artifacts.items()
                       if artifact.get("backend_node_id") and artifact["kind"] == "terrain"]
    manifest["surface_vertices"] = sum(len(obj.data.vertices) for obj in terrain_objects)
    manifest["surface_faces"] = sum(len(obj.data.polygons) for obj in terrain_objects)
    manifest["source_asset_count"] = sum(obj.get("og_role", "").startswith("asset:") for obj in bpy.data.objects)
    manifest["selected_faces"] = sum(a.get("allowed_faces", 0) for a in result.artifacts.values())
    grass_nodes = [node for node in request["scene"]["nodes"] if node["operator"] in ("blender.grass", "external.infinigen.grass")]
    manifest["vegetation_params"] = grass_nodes[0]["params"] if len(grass_nodes) == 1 else {n["id"]: n["params"] for n in grass_nodes}
    if bundle:
        manifest["manual_region"] = bundle["regions"][0] if len(bundle["regions"]) == 1 else bundle["regions"]
    write_json(session.out/"objects.json", manifest)
    validation = {"status": "passed", "passed": True, "checks": checks, "preserved_backend_ids": preserved,
                  "removed_backend_ids": session.removed_ids,
                  "preserved_snapshots_equal": True, "rebuilt_ids": result.rebuilt_ids,
                  "preserved_ids": result.preserved_ids, "actual_instances": len(flat_instances)}
    write_json(session.out/"validation.json", validation)
    return state


def verify_directory(out):
    out = Path(out).resolve()
    request = json.loads((out/"request.json").read_text())
    expected = json.loads((out/"objects.json").read_text())
    state = json.loads((out/"state.json").read_text())
    bpy.ops.wm.open_mainfile(filepath=str(out/"scene.blend"))
    require(json.loads(bpy.data.texts["OutdoorGraph.GraphRequest.json"].as_string()) == request, "Embedded request differs")
    require(json.loads(bpy.data.texts["OutdoorGraph.Execution.json"].as_string()) == state, "Embedded execution report differs")
    current = snapshots()
    require(current == expected["nodes"], "Reopened actual geometry/instance/metadata snapshot differs")
    previous_file = request.get("previous_blend")
    if expected["preserved_backend_ids"]:
        require(previous_file and Path(previous_file).is_file(), "Missing prior scene for independent preservation audit")
        bpy.ops.wm.open_mainfile(filepath=str(Path(previous_file).resolve()))
        previous = snapshots()
        for node_id in expected["preserved_backend_ids"]:
            require(previous.get(node_id) == current[node_id], "Independent prior/current node comparison failed: "+node_id)
        bpy.ops.wm.open_mainfile(filepath=str(out/"scene.blend"))
    checks = geometry_validation(state["artifacts"], request["scene"])
    image = bpy.data.images.load(str(out/"render.png"), check_existing=False)
    pixels = np.asarray(image.pixels[:], dtype=float)
    require(tuple(image.size) == (expected["render_width"], expected["render_height"]), "Decoded render dimensions differ")
    require(np.isfinite(pixels).all() and float(np.var(pixels)) > 1e-8, "Render is invalid or constant")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(out/"scene.glb"))
    imported = [obj for obj in bpy.data.objects if obj.type == "MESH"]
    faces = sum(len(obj.data.polygons) for obj in imported)
    require(faces > 0, "GLB actual import has no faces")
    imported_ids = [obj.get("og_object_id") for obj in imported]
    require(all(imported_ids) and len(imported_ids) == len(set(imported_ids)), "GLB extras missing/duplicate IDs")
    report = {"status": "passed", "passed": True, "actual_nodes": len(current), "actual_instances": expected["vegetation_instance_count"],
              "preserved_backend_ids": expected["preserved_backend_ids"], "independent_previous_comparison": True,
              "checks": checks, "decoded_render_variance": float(np.var(pixels)),
              "glb_mesh_objects": len(imported), "glb_faces": faces, "glb_object_ids": imported_ids}
    write_json(out/"reopen_verification.json", report)
    return report


def run_bundle(bundle_path, out, infinigen_source):
    bundle_path = Path(bundle_path).resolve()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    request = {"scene": bundle["scene"], "bundle": bundle, "bundle_path": str(bundle_path),
               "previous_artifacts": {}, "previous_blend": None,
               "render": {"width": 640, "height": 480, "samples": 16}}
    return run_request(request, out, infinigen_source)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--infinigen-source", type=Path)
    parser.add_argument("--verify", type=Path)
    argv = sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    if args.infinigen_source:
        sys.path.insert(0, str(args.infinigen_source.resolve()))
    if args.verify:
        result = verify_directory(args.verify)
        print(json.dumps({"status": result["status"], "actual_instances": result["actual_instances"]}))
        return
    require(args.request and args.out and args.infinigen_source, "Provide --request, --out and --infinigen-source")
    request = json.loads(args.request.read_text(encoding="utf-8"))
    try:
        result = run_request(request, args.out, args.infinigen_source, args.request)
    except Exception as error:
        args.out.mkdir(parents=True, exist_ok=True)
        write_json(args.out/"failure.json", {"status": "failed", "error": str(error)})
        raise
    print(json.dumps({"status": result["status"], "rebuilt_ids": result["rebuilt_ids"], "preserved_ids": result["preserved_ids"]}))


if __name__ == "__main__":
    main()

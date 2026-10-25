"""Deterministic proxy operators, deliberately not yet photorealistic.

The contract is the interesting part: stable inputs/outputs, shared units and
explicit upstream artifacts. Production Blender/Infinigen adapters come later.
"""

from __future__ import annotations

from copy import deepcopy
import math
import random
from typing import Any, Callable

from outdoorgraph.ir import Node

Artifact = dict[str, Any]
Operator = Callable[[Node, dict[str, Artifact]], Artifact]
REGISTRY: dict[str, Operator] = {}


def register(name: str) -> Callable[[Operator], Operator]:
    def decorator(fn: Operator) -> Operator:
        if name in REGISTRY:
            raise ValueError(f"Operator already exists: {name}")
        REGISTRY[name] = fn
        return fn
    return decorator


def get_artifact(inputs: dict[str, Artifact], kind: str) -> Artifact:
    matches = [item for item in inputs.values() if item["kind"] == kind]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {kind} dependency, found {len(matches)}")
    return matches[0]


def height_at(terrain: Artifact, x: float, y: float) -> float:
    p = terrain["params"]
    base = (float(p.get("base_height_m", 0))
            + float(p.get("slope_x", 0)) * x
            + float(p.get("slope_y", 0)) * y)
    scale = max(1e-3, float(p.get("noise_scale_m", 40)))
    rough = float(p.get("noise_amplitude_m", 0)) * (
        math.sin(x / scale + float(p.get("phase_x", 0)))
        * math.cos(y / scale + float(p.get("phase_y", 0))))
    bump = p.get("local_bump", {})
    if bump:
        bx, by = float(bump.get("x", 0)), float(bump.get("y", 0))
        sigma = max(1e-3, float(bump.get("sigma_m", 15)))
        amplitude = float(bump.get("amplitude_m", 0))
        rough += amplitude * math.exp(-((x-bx)**2 + (y-by)**2)/(2*sigma**2))
    return base + rough


def point_to_segment_distance(x: float, y: float, a: list[float], b: list[float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    denominator = dx * dx + dy * dy
    if denominator < 1e-12:
        return math.hypot(x-a[0], y-a[1])
    t = max(0.0, min(1.0, ((x-a[0])*dx+(y-a[1])*dy)/denominator))
    return math.hypot(x-(a[0]+t*dx), y-(a[1]+t*dy))


def distance_to_road(x: float, y: float, road: Artifact) -> float:
    points = road["points"]
    return min(point_to_segment_distance(x, y, p, q) for p,q in zip(points[:-1], points[1:]))


@register("terrain.heightfield")
def terrain_heightfield(node: Node, inputs: dict[str, Artifact]) -> Artifact:
    if inputs:
        raise ValueError("Terrain operator does not take dependencies in v0.1")
    return {"kind":"terrain", "params":deepcopy(node.params), "bounds_xy":list(node.bounds_xy)}


@register("road.polyline")
def road_polyline(node: Node, inputs: dict[str, Artifact]) -> Artifact:
    terrain = get_artifact(inputs, "terrain")
    xy_points = node.params["control_points_xy"]
    if len(xy_points) < 2:
        raise ValueError("Road requires at least two control points")
    width = float(node.params.get("width_m", 4))
    if not math.isfinite(width) or width <= 0:
        raise ValueError("Road width_m must be finite and positive")
    offset = float(node.params.get("height_offset_m", 0))
    points = [[float(x), float(y), height_at(terrain,float(x),float(y)) + offset]
              for x,y in xy_points]
    return {"kind":"road", "width_m":width, "points":points}


@register("vegetation.scatter")
def vegetation_scatter(node: Node, inputs: dict[str, Artifact]) -> Artifact:
    terrain = get_artifact(inputs, "terrain")
    roads = [item for item in inputs.values() if item["kind"] == "road"]
    rng = random.Random(node.seed)
    count = int(node.params.get("count", 40))
    if not 0 <= count <= 10000:
        raise ValueError("Invalid requested instance count")
    min_spacing = float(node.params.get("min_spacing_m", 4))
    buffer = float(node.params.get("road_buffer_m", 2))
    noise_scale = max(1e-3, float(node.params.get("noise_scale_m", 32)))
    x0,y0,x1,y1 = node.bounds_xy
    positions: list[list[float]] = []
    attempts = 0
    max_attempts = max(100, 100 * count)
    while len(positions) < count and attempts < max_attempts:
        attempts += 1
        x,y = rng.uniform(x0,x1),rng.uniform(y0,y1)
        # Stationary field: normalized acceptance probability, not pixel noise.
        density = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(x/noise_scale) * math.cos(y/noise_scale))
        if rng.random() > density:
            continue
        if any(distance_to_road(x,y,road) < road["width_m"] / 2 + buffer for road in roads):
            continue
        if any(math.hypot(x-p[0], y-p[1]) < min_spacing for p in positions):
            continue
        positions.append([x,y,height_at(terrain,x,y)])
    return {"kind":"vegetation", "species":node.params.get("species", "tree_proxy"),
            "positions":positions, "requested_count":count, "attempts":attempts}


@register("building.proxy")
def building_proxy(node: Node, inputs: dict[str, Artifact]) -> Artifact:
    terrain = get_artifact(inputs, "terrain")
    x, y = [float(a) for a in node.params["position_xy"]]
    size = [float(a) for a in node.params.get("size_m", [8,8,7])]
    return {"kind":"building", "position_xyz":[x,y,height_at(terrain,x,y)], "size_m":size}


@register("background.constant")
def background_constant(node: Node, inputs: dict[str, Artifact]) -> Artifact:
    if inputs:
        raise ValueError("Background does not take dependencies")
    return {"kind":"background", "color":node.params.get("color", "#a0c9e6")}

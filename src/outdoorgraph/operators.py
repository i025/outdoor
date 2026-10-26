"""Deterministic geometry shared by proxy execution and Blender operators."""

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


def finite_number(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def height_at(terrain: Artifact, x: float, y: float) -> float:
    p = terrain["params"]
    x, y = finite_number(x, "terrain x"), finite_number(y, "terrain y")
    base = (finite_number(p.get("base_height_m", 0), "base_height_m")
            + finite_number(p.get("slope_x", 0), "slope_x") * x
            + finite_number(p.get("slope_y", 0), "slope_y") * y)
    scale = finite_number(p.get("noise_scale_m", 40), "noise_scale_m")
    if scale <= 0:
        raise ValueError("noise_scale_m must be positive")
    rough = finite_number(p.get("noise_amplitude_m", 0), "noise_amplitude_m") * (
        math.sin(x / scale + finite_number(p.get("phase_x", 0), "phase_x"))
        * math.cos(y / scale + finite_number(p.get("phase_y", 0), "phase_y")))
    bump = p.get("local_bump", {})
    if bump:
        bx = finite_number(bump.get("x", 0), "local_bump.x")
        by = finite_number(bump.get("y", 0), "local_bump.y")
        amplitude = finite_number(bump.get("amplitude_m", 0), "local_bump.amplitude_m")
        profile = bump.get("profile", "gaussian")
        if profile == "compact":
            radius = finite_number(bump.get("radius_m", bump.get("sigma_m", 15)),
                                   "local_bump.radius_m")
            if radius <= 0:
                raise ValueError("local_bump.radius_m must be positive")
            radial = math.hypot(x - bx, y - by) / radius
            # Value, slope and curvature all reach zero at the support boundary.
            if radial < 1:
                rough += amplitude * (1 - radial * radial) ** 3
        elif profile == "gaussian":
            sigma = finite_number(bump.get("sigma_m", 15), "local_bump.sigma_m")
            if sigma <= 0:
                raise ValueError("local_bump.sigma_m must be positive")
            radial = math.hypot(x - bx, y - by) / sigma
            rough += amplitude * math.exp(-radial * radial / 2)
        else:
            raise ValueError(f"Unknown local_bump profile: {profile}")
    return finite_number(base + rough, "terrain height")


def sample_polyline(points: list[list[float]], spacing_m: float) -> list[list[float]]:
    """Sample the actual piecewise-linear XYZ curve, including each midpoint.

    Interpolating z here is intentional: constraint validation must measure the
    geometry that is rendered, rather than reprojecting it onto the terrain.
    """
    spacing = finite_number(spacing_m, "sample_spacing_m")
    if spacing <= 0:
        raise ValueError("sample_spacing_m must be positive")
    if len(points) < 2:
        raise ValueError("Road requires at least two points")
    clean = []
    for point in points:
        if len(point) != 3:
            raise ValueError("Road points must contain XYZ coordinates")
        clean.append([finite_number(value, "road coordinate") for value in point])
    samples = [clean[0]]
    for start, end in zip(clean[:-1], clean[1:]):
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        steps = max(2, math.ceil(length / spacing))
        samples.extend([[a + (b - a) * step / steps for a, b in zip(start, end)]
                        for step in range(1, steps + 1)])
    return samples


def point_to_segment_distance(x: float, y: float, a: list[float], b: list[float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    denominator = dx * dx + dy * dy
    if denominator < 1e-12:
        return math.hypot(x-a[0], y-a[1])
    t = max(0.0, min(1.0, ((x-a[0])*dx+(y-a[1])*dy)/denominator))
    return math.hypot(x-(a[0]+t*dx), y-(a[1]+t*dy))


def distance_to_road(x: float, y: float, road: Artifact) -> float:
    points = road.get("sampled_points", road["points"])
    return min(point_to_segment_distance(x, y, p, q) for p,q in zip(points[:-1], points[1:]))


@register("terrain.heightfield")
def terrain_heightfield(node: Node, inputs: dict[str, Artifact]) -> Artifact:
    if inputs:
        raise ValueError("Terrain operator does not take dependencies in v0.1")
    artifact = {"kind":"terrain", "params":deepcopy(node.params), "bounds_xy":list(node.bounds_xy)}
    height_at(artifact, node.bounds_xy[0], node.bounds_xy[1])
    return artifact


@register("road.polyline")
def road_polyline(node: Node, inputs: dict[str, Artifact]) -> Artifact:
    terrain = get_artifact(inputs, "terrain")
    xy_points = node.params["control_points_xy"]
    if len(xy_points) < 2:
        raise ValueError("Road requires at least two control points")
    width = float(node.params.get("width_m", 4))
    if not math.isfinite(width) or width <= 0:
        raise ValueError("Road width_m must be finite and positive")
    offset = finite_number(node.params.get("height_offset_m", 0), "height_offset_m")
    spacing = finite_number(node.params.get("sample_spacing_m", 1), "sample_spacing_m")
    points = []
    for point in xy_points:
        if len(point) != 2:
            raise ValueError("Road control_points_xy must contain XY coordinates")
        x, y = [finite_number(value, "road control coordinate") for value in point]
        points.append([x, y, height_at(terrain, x, y) + offset])
    if any(p[:2] == q[:2] for p, q in zip(points[:-1], points[1:])):
        raise ValueError("Road consecutive control points must be distinct")
    sampled = sample_polyline(points, spacing)
    for point in sampled:
        point[2] = height_at(terrain, point[0], point[1]) + offset
    return {"kind":"road", "width_m":width, "points":points,
            "sampled_points":sampled, "sampling_spacing_m":spacing}


@register("vegetation.scatter")
def vegetation_scatter(node: Node, inputs: dict[str, Artifact]) -> Artifact:
    terrain = get_artifact(inputs, "terrain")
    roads = [item for item in inputs.values() if item["kind"] == "road"]
    rng = random.Random(node.seed)
    count = int(node.params.get("count", 40))
    if not 0 <= count <= 10000:
        raise ValueError("Invalid requested instance count")
    min_spacing = finite_number(node.params.get("min_spacing_m", 4), "min_spacing_m")
    buffer = finite_number(node.params.get("road_buffer_m", 2), "road_buffer_m")
    noise_scale = finite_number(node.params.get("noise_scale_m", 32), "noise_scale_m")
    if min_spacing < 0 or buffer < 0 or noise_scale <= 0:
        raise ValueError("Vegetation spacing/buffer must be nonnegative and noise scale positive")
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
    x, y = [finite_number(a, "position_xy") for a in node.params["position_xy"]]
    size = [finite_number(a, "size_m") for a in node.params.get("size_m", [8,8,7])]
    if len(size) != 3 or any(value <= 0 for value in size):
        raise ValueError("Building size_m must have three positive dimensions")
    return {"kind":"building", "position_xyz":[x,y,height_at(terrain,x,y)], "size_m":size}


@register("background.constant")
def background_constant(node: Node, inputs: dict[str, Artifact]) -> Artifact:
    if inputs:
        raise ValueError("Background does not take dependencies")
    return {"kind":"background", "color":node.params.get("color", "#a0c9e6")}

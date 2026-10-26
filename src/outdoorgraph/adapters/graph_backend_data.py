"""Small geometry interchange helpers shared by the graph Blender worker/tests.

These helpers discretize existing artifacts for rendering; they do not generate
vegetation or replace the registered heightfield/road algorithms.
"""
from __future__ import annotations

import math


def grid_axes(bounds, spacing):
    if len(bounds) != 4 or not all(math.isfinite(float(v)) for v in bounds):
        raise ValueError("Finite XY bounds required")
    x0, y0, x1, y1 = map(float, bounds)
    if x1 <= x0 or y1 <= y0 or not math.isfinite(spacing) or spacing <= 0:
        raise ValueError("Positive bounds and grid spacing required")
    nx, ny = math.ceil((x1-x0)/spacing), math.ceil((y1-y0)/spacing)
    if (nx+1)*(ny+1) > 200000:
        raise ValueError("Heightfield mesh exceeds worker vertex budget")
    return ([x0+(x1-x0)*i/nx for i in range(nx+1)],
            [y0+(y1-y0)*j/ny for j in range(ny+1)])


def heightfield_mesh(artifact, spacing, height_fn):
    xs, ys = grid_axes(artifact["bounds_xy"], spacing)
    vertices = [[x, y, float(height_fn(artifact, x, y))] for y in ys for x in xs]
    faces = []
    for j in range(len(ys)-1):
        for i in range(len(xs)-1):
            a = j*len(xs)+i
            faces.extend([[a, a+1, a+len(xs)+1], [a, a+len(xs)+1, a+len(xs)]])
    return vertices, faces


def ribbon_xy(points, width, spacing):
    if len(points) < 2 or not math.isfinite(width) or width <= 0:
        raise ValueError("A positive-width road with at least two points is required")
    if not math.isfinite(spacing) or spacing <= 0:
        raise ValueError("Positive road cross-section spacing required")
    across = max(1, math.ceil(width/spacing))
    if len(points)*(across+1) > 200000:
        raise ValueError("Road mesh exceeds worker vertex budget")
    vertices = []
    for i, p in enumerate(points):
        before, after = points[max(0, i-1)], points[min(len(points)-1, i+1)]
        dx, dy = after[0]-before[0], after[1]-before[1]
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            raise ValueError("Degenerate road direction")
        normal = (-dy/length, dx/length)
        for j in range(across+1):
            offset = width*(j/across-.5)
            vertices.append([p[0]+normal[0]*offset, p[1]+normal[1]*offset])
    faces = []
    for i in range(len(points)-1):
        for j in range(across):
            a = i*(across+1)+j
            faces.extend([[a, a+1, a+across+2], [a, a+across+2, a+across+1]])
    return vertices, faces


def allowed_region_faces(vertices, faces, bounds, roads, buffer, distance_fn, inherited=None):
    """Conservative FACE mask; native Infinigen still distributes every point.

    Distance to a road is 1-Lipschitz. Requiring centroid distance minus the
    triangle's XY radius >= clearance guarantees the whole triangle is outside
    the road buffer, including corners between the vertex samples.
    """
    x0, y0, x1, y1 = bounds
    result = []
    for index, face in enumerate(faces):
        triangle = [vertices[i] for i in face]
        allow = inherited is None or bool(inherited[index])
        allow = allow and all(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in triangle)
        cx, cy = sum(p[0] for p in triangle)/3, sum(p[1] for p in triangle)/3
        radius = max(math.hypot(p[0]-cx, p[1]-cy) for p in triangle)
        for road in roads:
            required = float(road["width_m"])/2 + buffer
            allow = allow and distance_fn(cx, cy, road)-radius >= required
        result.append(bool(allow))
    return result


def instance_owner(instance):
    """Read ownership immediately: Blender depsgraph iterator structs expire."""
    parent = instance.parent.original if instance.parent else instance.object.original
    return parent.get("og_node_id")

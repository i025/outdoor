"""Deterministic, geometry-aware constraint checks.

A failing check is reported, NOT silently repaired. Repair policy and
constraint solving are next research steps; this code is deliberately honest.
"""

from __future__ import annotations

import math
from typing import Any

from outdoorgraph.ir import Constraint, Scene
from outdoorgraph.operators import distance_to_road, finite_number, height_at, sample_polyline


def _terrain_continuity(first: dict[str, Any], second: dict[str, Any],
                        params: dict[str, Any], limit: float) -> dict[str, Any]:
    ax0, ay0, ax1, ay1 = first["bounds_xy"]
    bx0, by0, bx1, by1 = second["bounds_xy"]
    boundary = None
    if ax1 == bx0 or bx1 == ax0:
        x = ax1 if ax1 == bx0 else ax0
        low, high = max(ay0, by0), min(ay1, by1)
        if high > low:
            boundary = [[x, low, 0], [x, high, 0]]
    elif ay1 == by0 or by1 == ay0:
        y = ay1 if ay1 == by0 else ay0
        low, high = max(ax0, bx0), min(ax1, bx1)
        if high > low:
            boundary = [[low, y, 0], [high, y, 0]]
    if boundary is None:
        return {"passed": False, "value": None, "limit": limit, "unit": "m",
                "sample_count": 0, "reason": "Terrain bounds do not share an adjacent edge"}
    samples = sample_polyline(boundary, params.get("sample_spacing_m", 0.25))
    errors = [abs(height_at(first, p[0], p[1]) - height_at(second, p[0], p[1]))
              for p in samples]
    worst = max(range(len(errors)), key=errors.__getitem__)
    return {"passed": errors[worst] <= limit, "value": errors[worst], "limit": limit,
            "unit": "m", "sample_count": len(samples), "boundary_xy": [p[:2] for p in boundary],
            "worst_point_xy": samples[worst][:2]}


def _road_topology(road: dict[str, Any], params: dict[str, Any],
                   limit: float) -> dict[str, Any]:
    points = road.get("sampled_points", road.get("points", []))
    report = {"passed": False, "value": None, "limit": limit, "unit": "m",
              "point_count": len(points)}
    if len(points) < 2:
        return {**report, "reason": "Road topology requires at least two points"}
    try:
        clean = []
        for point in points:
            if len(point) != 3:
                return {**report, "reason": "Road topology requires XYZ points"}
            clean.append([finite_number(value, "road topology coordinate") for value in point])
        lengths = [math.hypot(b[0] - a[0], b[1] - a[1])
                   for a, b in zip(clean[:-1], clean[1:])]
        if min(lengths) <= 0:
            return {**report, "reason": "Road topology contains a zero-length XY segment"}
        max_step = params.get("max_segment_length_m", road.get("sampling_spacing_m"))
        if max_step is not None:
            max_step = finite_number(max_step, "max_segment_length_m")
            if max_step <= 0:
                return {**report, "reason": "max_segment_length_m must be positive"}
            if max(lengths) > max_step + 1e-9:
                return {**report, "value": max(lengths), "limit": max_step,
                        "reason": "Road topology exceeds the permitted segment length"}
        endpoint_error = 0.0
        required = params.get("required_endpoints_xy")
        if required is not None:
            if len(required) != 2 or any(len(point) != 2 for point in required):
                return {**report, "reason": "required_endpoints_xy requires start and end XY points"}
            endpoints = [[finite_number(value, "required endpoint coordinate") for value in point]
                         for point in required]
            endpoint_error = max(math.hypot(actual[0] - expected[0], actual[1] - expected[1])
                                 for actual, expected in zip([clean[0], clean[-1]], endpoints))
        return {**report, "passed": endpoint_error <= limit, "value": endpoint_error,
                "segment_count": len(lengths), "minimum_segment_length_m": min(lengths),
                "maximum_segment_length_m": max(lengths),
                **({"reason": "Road endpoints do not match the required endpoints"}
                   if endpoint_error > limit else {})}
    except ValueError as error:
        return {**report, "reason": str(error)}


def _check(constraint: Constraint, artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ids = constraint.node_ids
    rule = constraint.rule
    limit = finite_number(constraint.params.get("tolerance_m", 0.05), "tolerance_m")
    if limit < 0:
        raise ValueError("tolerance_m must be nonnegative")

    if rule == "terrain_continuity":
        return _terrain_continuity(artifacts[ids[0]], artifacts[ids[1]], constraint.params, limit)

    if rule == "road_topology":
        return _road_topology(artifacts[ids[0]], constraint.params, limit)

    if rule == "ground_contact":
        building, terrain = (artifacts[ids[0]], artifacts[ids[1]])
        x,y,z = [finite_number(value, "ground_contact coordinate")
                 for value in building["position_xyz"]]
        error = abs(z-height_at(terrain,x,y))
        return {"passed": error <= limit, "value": error, "limit": limit, "unit":"m"}

    if rule == "road_contact":
        road, terrain = (artifacts[ids[0]], artifacts[ids[1]])
        points = road.get("sampled_points", road["points"])
        samples = sample_polyline(points, constraint.params.get("sample_spacing_m", 0.25))
        errors = [abs(p[2] - height_at(terrain, p[0], p[1])) for p in samples]
        worst = max(range(len(errors)), key=errors.__getitem__)
        error = errors[worst]
        return {"passed": error <= limit, "value": error, "limit": limit, "unit":"m",
                "sample_count": len(samples), "worst_point_xyz": samples[worst]}

    if rule == "road_grade":
        road = artifacts[ids[0]]
        points = sample_polyline(road.get("sampled_points", road["points"]),
                                 constraint.params.get("sample_spacing_m", 0.25))
        grades = []
        for p,q in zip(points[:-1], points[1:]):
            length = math.hypot(q[0]-p[0],q[1]-p[1])
            if length <= 1e-9:
                raise ValueError("road_grade requires distinct consecutive XY points")
            grades.append(abs(q[2]-p[2])/length)
        grade = max(grades, default=0)
        max_grade = finite_number(constraint.params.get("max_grade", 0.2), "max_grade")
        if max_grade < 0:
            raise ValueError("max_grade must be nonnegative")
        return {"passed":grade<=max_grade,"value":grade,"limit":max_grade,"unit":"rise/run"}

    if rule == "vegetation_road_clearance":
        vegetation, road = artifacts[ids[0]], artifacts[ids[1]]
        buffer = finite_number(constraint.params.get("buffer_m", 2), "buffer_m")
        width = finite_number(road["width_m"], "road width_m")
        if buffer < 0 or width <= 0:
            raise ValueError("buffer_m must be nonnegative and road width_m positive")
        clearance_required = width/2 + buffer
        sample_polyline(road.get("sampled_points", road["points"]),
                        constraint.params.get("sample_spacing_m", 0.25))
        for point in vegetation["positions"]:
            for value in point:
                finite_number(value, "vegetation coordinate")
        if not vegetation["positions"]:
            # Empty vegetation is separately flagged by requested_count check.
            return {"passed":True,"value":None,"limit":clearance_required,"unit":"m"}
        actual = min(distance_to_road(p[0],p[1],road) for p in vegetation["positions"])
        return {"passed":actual+1e-9>=clearance_required,"value":actual,
                "limit":clearance_required,"unit":"m"}

    if rule == "vegetation_min_count":
        vegetation = artifacts[ids[0]]
        minimum = int(constraint.params.get("min_count", 1))
        count = len(vegetation["positions"])
        return {"passed":count>=minimum,"value":count,"limit":minimum,"unit":"instances"}

    raise ValueError(f"Unknown constraint rule: {rule}")


def evaluate_constraints(scene: Scene, artifacts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    reports = []
    for constraint in scene.constraints:
        if any(item not in artifacts for item in constraint.node_ids):
            raise ValueError(f"Missing artifacts for constraint {constraint.id}")
        report = _check(constraint, artifacts)
        reports.append({"id":constraint.id, "rule":constraint.rule,
                        "hard":constraint.hard, "nodes":constraint.node_ids, **report})
    return reports

"""Deterministic, geometry-aware constraint checks.

A failing check is reported, NOT silently repaired. Repair policy and
constraint solving are next research steps; this code is deliberately honest.
"""

from __future__ import annotations

import math
from typing import Any

from outdoorgraph.ir import Constraint, Scene
from outdoorgraph.operators import distance_to_road, height_at


def _check(constraint: Constraint, artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ids = constraint.node_ids
    rule = constraint.rule
    limit = float(constraint.params.get("tolerance_m", 0.05))

    if rule == "ground_contact":
        building, terrain = (artifacts[ids[0]], artifacts[ids[1]])
        x,y,z = building["position_xyz"]
        error = abs(z-height_at(terrain,x,y))
        return {"passed": error <= limit, "value": error, "limit": limit, "unit":"m"}

    if rule == "road_contact":
        road, terrain = (artifacts[ids[0]], artifacts[ids[1]])
        error = max(abs(p[2]-height_at(terrain,p[0],p[1])) for p in road["points"])
        return {"passed": error <= limit, "value": error, "limit": limit, "unit":"m"}

    if rule == "road_grade":
        road = artifacts[ids[0]]
        points = road["points"]
        grades = []
        for p,q in zip(points[:-1], points[1:]):
            length = math.hypot(q[0]-p[0],q[1]-p[1])
            grades.append(abs(q[2]-p[2])/length if length > 1e-9 else float("inf"))
        grade = max(grades, default=0)
        max_grade = float(constraint.params.get("max_grade", 0.2))
        return {"passed":grade<=max_grade,"value":grade,"limit":max_grade,"unit":"rise/run"}

    if rule == "vegetation_road_clearance":
        vegetation, road = artifacts[ids[0]], artifacts[ids[1]]
        buffer = float(constraint.params.get("buffer_m", 2))
        clearance_required = road["width_m"]/2 + buffer
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

"""Structural validation before rendering, editing or trusting Agent output."""

from __future__ import annotations

import math

from outdoorgraph.ir import Scene


_CONSTRAINT_NODE_COUNTS = {
    "ground_contact": 2,
    "road_contact": 2,
    "road_grade": 1,
    "vegetation_road_clearance": 2,
    "vegetation_min_count": 1,
}


class SceneValidationError(ValueError):
    pass


def topological_ids(scene: Scene) -> list[str]:
    """Stable topological order across validated, directed runtime dependencies."""
    ids = [node.id for node in scene.nodes]
    if len(set(ids)) != len(ids):
        raise SceneValidationError("Duplicate node IDs")
    followers: dict[str, list[str]] = {item: [] for item in ids}
    incoming: dict[str, int] = {item: 0 for item in ids}
    for edge in scene.valid_edges():
        if edge.source_id not in followers or edge.target_id not in followers:
            raise SceneValidationError(f"Invalid edge endpoint: {edge.source_id}->{edge.target_id}")
        followers[edge.source_id].append(edge.target_id)
        incoming[edge.target_id] += 1
    queue = [item for item in ids if incoming[item] == 0]
    order: list[str] = []
    while queue:
        current = queue.pop(0)
        order.append(current)
        for follower in followers[current]:
            incoming[follower] -= 1
            if incoming[follower] == 0:
                queue.append(follower)
    if len(order) != len(ids):
        raise SceneValidationError("Cycle among validated dependencies")
    return order


def validate_scene(scene: Scene) -> None:
    if scene.schema_version != "0.1":
        raise SceneValidationError("Unsupported schema version")
    if scene.world.get("units") != "m" or scene.world.get("axis") != "z_up":
        raise SceneValidationError("v0.1 requires meters and local z-up coordinates")
    ids = [node.id for node in scene.nodes]
    if not ids or len(ids) != len(set(ids)):
        raise SceneValidationError("Scene needs nodes with unique IDs")
    ev_ids = [item.id for item in scene.evidence]
    if len(set(ev_ids)) != len(ev_ids):
        raise SceneValidationError("Duplicate evidence IDs")
    for ev in scene.evidence:
        if not math.isfinite(ev.confidence) or not 0 <= ev.confidence <= 1:
            raise SceneValidationError(f"Invalid evidence confidence: {ev.id}")
        if ev.status not in {"observed", "inferred", "generated"}:
            raise SceneValidationError(f"Invalid evidence status: {ev.id}")
    for node in scene.nodes:
        if len(node.bounds_xy) != 4 or not all(math.isfinite(float(v)) for v in node.bounds_xy):
            raise SceneValidationError(f"Invalid bounds: {node.id}")
        x0, y0, x1, y1 = node.bounds_xy
        if x1 <= x0 or y1 <= y0:
            raise SceneValidationError(f"Non-positive bounds: {node.id}")
        for item in node.evidence_ids:
            if item not in ev_ids:
                raise SceneValidationError(f"Missing evidence {item} on node {node.id}")
    for edge in scene.dependencies:
        if edge.source_id not in ids or edge.target_id not in ids:
            raise SceneValidationError("Dependency points to a missing node")
        if not math.isfinite(edge.confidence) or not 0 <= edge.confidence <= 1:
            raise SceneValidationError("Invalid dependency confidence")
        if edge.status not in {"proposed", "validated", "rejected"}:
            raise SceneValidationError("Invalid dependency status")
        for item in edge.evidence_ids:
            if item not in ev_ids:
                raise SceneValidationError("Missing dependency evidence")
    constraint_ids: set[str] = set()
    for constraint in scene.constraints:
        if constraint.id in constraint_ids:
            raise SceneValidationError("Duplicate constraint ID")
        constraint_ids.add(constraint.id)
        expected = _CONSTRAINT_NODE_COUNTS.get(constraint.rule)
        if expected is not None and len(constraint.node_ids) != expected:
            raise SceneValidationError(
                f"Invalid constraint node count: {constraint.id} "
                f"({constraint.rule} requires {expected} nodes)"
            )
        if not constraint.node_ids or any(item not in ids for item in constraint.node_ids):
            raise SceneValidationError(f"Invalid constraint nodes: {constraint.id}")
        for item in constraint.evidence_ids:
            if item not in ev_ids:
                raise SceneValidationError(f"Missing constraint evidence: {constraint.id}")
    topological_ids(scene)

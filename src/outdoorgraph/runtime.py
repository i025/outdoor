"""Validated dependency traversal, deterministic regeneration and incremental edits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from outdoorgraph.constraints import evaluate_constraints
from outdoorgraph.ir import Scene
from outdoorgraph.operators import REGISTRY
from outdoorgraph.validation import validate_scene, topological_ids


@dataclass
class RunResult:
    artifacts: dict[str, dict[str, Any]]
    rebuilt_ids: list[str]
    preserved_ids: list[str]
    constraint_reports: list[dict[str, Any]]

    @property
    def passed(self) -> bool:
        return all(item["passed"] or not item["hard"] for item in self.constraint_reports)

    def to_dict(self) -> dict[str, Any]:
        return {"artifacts": self.artifacts, "rebuilt_ids": self.rebuilt_ids,
                "preserved_ids":self.preserved_ids, "passed":self.passed,
                "constraint_reports":self.constraint_reports}


def downstream(scene: Scene, changed_ids: set[str]) -> set[str]:
    """Conservative closure over validated runtime edges (NOT proposed hypotheses)."""
    ids = {node.id for node in scene.nodes}
    if not changed_ids.issubset(ids):
        raise KeyError(f"Unknown edited nodes: {sorted(changed_ids-ids)}")
    followers: dict[str, set[str]] = {item:set() for item in ids}
    for edge in scene.valid_edges():
        followers[edge.source_id].add(edge.target_id)
    affected = set(changed_ids)
    queue = list(changed_ids)
    while queue:
        for child in sorted(followers[queue.pop(0)]):
            if child not in affected:
                affected.add(child)
                queue.append(child)
    return affected


def execute(scene: Scene, previous: dict[str, dict[str, Any]] | None = None,
            changed_ids: set[str] | None = None) -> RunResult:
    """Build initial scene, or selectively rebuild validated dependents.

    `previous` must be from this same scene graph version. Structural edits
    require the caller to revalidate and, if necessary, do a fresh build.
    """
    validate_scene(scene)
    order = topological_ids(scene)
    incoming: dict[str, list[str]] = {n.id:[] for n in scene.nodes}
    for dep in scene.valid_edges():
        incoming[dep.target_id].append(dep.source_id)
    if previous is None or changed_ids is None:
        rebuild = set(order)
        artifacts: dict[str, dict[str, Any]] = {}
    else:
        if set(previous) != set(order):
            raise ValueError("Previous artifact IDs do not match scene graph; rebuild all")
        rebuild = downstream(scene, changed_ids)
        artifacts = dict(previous)
    rebuilt, preserved = [], []
    for node_id in order:
        if node_id not in rebuild:
            preserved.append(node_id)
            continue
        node = scene.get_node(node_id)
        fn = REGISTRY.get(node.operator)
        if fn is None:
            raise ValueError(f"Unknown procedural operator: {node.operator}")
        inputs = {src:artifacts[src] for src in incoming[node_id]}
        artifacts[node_id] = fn(node, inputs)
        rebuilt.append(node_id)
    checks = evaluate_constraints(scene, artifacts)
    return RunResult(artifacts, rebuilt, preserved, checks)

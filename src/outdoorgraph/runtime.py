"""Backward-compatible proxy entry point over the unified graph executor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from outdoorgraph.ir import Scene


@dataclass
class RunResult:
    artifacts: dict[str, dict[str, Any]]
    rebuilt_ids: list[str]
    preserved_ids: list[str]
    constraint_reports: list[dict[str, Any]]
    status: str = "completed"
    node_reports: list[dict[str, Any]] = field(default_factory=list)
    dependency_reports: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    scene: Scene | None = None

    @property
    def passed(self) -> bool:
        return (self.status == "completed" and not self.failures
                and all(item["passed"] or not item["hard"] for item in self.constraint_reports))

    @property
    def updated_ids(self) -> list[str]:
        return self.rebuilt_ids

    @property
    def retained_ids(self) -> list[str]:
        return self.preserved_ids

    def to_dict(self) -> dict[str, Any]:
        value = {"artifacts": self.artifacts, "rebuilt_ids": self.rebuilt_ids,
                 "preserved_ids": self.preserved_ids, "passed": self.passed,
                 "constraint_reports": self.constraint_reports, "status": self.status,
                 "node_reports": self.node_reports, "dependency_reports": self.dependency_reports,
                 "failures": self.failures, "updated_ids": self.updated_ids,
                 "retained_ids": self.retained_ids}
        if self.scene is not None:
            value["scene"] = self.scene.to_dict()
        return value


def downstream(scene: Scene, changed_ids: set[str]) -> set[str]:
    """Conservative closure over candidate execution edges, excluding hypotheses."""
    ids = {node.id for node in scene.nodes}
    if not changed_ids.issubset(ids):
        raise KeyError(f"Unknown edited nodes: {sorted(changed_ids - ids)}")
    followers: dict[str, set[str]] = {item: set() for item in ids}
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
    """Keep legacy exceptions while using contracts and automatic cache invalidation."""
    from outdoorgraph.execution import execute as unified_execute

    # Old callers with arbitrary, incomplete dictionaries retain the established
    # diagnostic. Partial signed caches use the new missing-cache rebuild path.
    if (previous and set(previous) != {node.id for node in scene.nodes}
            and any(not isinstance(item, dict) or "_execution" not in item
                    for item in previous.values())):
        raise ValueError("Previous artifact IDs do not match scene graph; rebuild all")
    return unified_execute(scene, previous, changed_ids, raise_on_error=True)

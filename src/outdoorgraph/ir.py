"""Minimal, explicit, JSON-serializable scene IR (v0.1).

Separate execution dependencies from spatial constraints and observational evidence.
A hypothesized edge is *not* silently treated as an executable dependency.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Evidence:
    id: str
    modality: str  # image, depth, camera, manual, inferred
    source: str
    confidence: float = 1.0
    status: str = "observed"  # observed, inferred, generated


@dataclass
class Node:
    id: str
    kind: str  # terrain, road, vegetation, building, background ...
    operator: str
    params: dict[str, Any] = field(default_factory=dict)
    bounds_xy: list[float] = field(default_factory=lambda: [0, 0, 1, 1])
    seed: int = 0
    evidence_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Dependency:
    source_id: str
    target_id: str
    relation: str
    confidence: float = 1.0
    status: str = "proposed"  # proposed, validated, executable, rejected
    evidence_ids: list[str] = field(default_factory=list)
    input_port: str | None = None
    output_port: str | None = None


@dataclass
class Constraint:
    id: str
    rule: str
    node_ids: list[str]
    params: dict[str, Any] = field(default_factory=dict)
    hard: bool = True
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class Scene:
    id: str
    nodes: list[Node]
    dependencies: list[Dependency] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    world: dict[str, Any] = field(default_factory=lambda: {"units": "m", "axis": "z_up"})
    schema_version: str = "0.1"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Scene":
        return cls(
            id=value["id"],
            nodes=[Node(**n) for n in value.get("nodes", [])],
            dependencies=[Dependency(**d) for d in value.get("dependencies", [])],
            constraints=[Constraint(**c) for c in value.get("constraints", [])],
            evidence=[Evidence(**e) for e in value.get("evidence", [])],
            world=value.get("world", {"units": "m", "axis": "z_up"}),
            schema_version=value.get("schema_version", "0.1"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def get_node(self, node_id: str) -> Node:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(node_id)

    def valid_edges(self) -> list[Dependency]:
        # Candidate runtime edges; the Planner must recheck contracts and gates.
        return [edge for edge in self.dependencies if edge.status in {"validated", "executable"}]

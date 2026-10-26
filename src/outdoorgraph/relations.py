"""Extensible, trusted semantic contracts for Scene IR dependencies.

Relation names describe a claim rather than permission. Every registered claim
has contract validators; physical claims additionally need geometry validators
after generation. Confidence and a serialized lifecycle status never replace
these checks. This registry is not a sandbox for untrusted Python callbacks.
"""

from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass
import json
from typing import Any, Callable

from outdoorgraph.ir import Constraint, Dependency, Node


RelationValidator = Callable[[Dependency, Node, Node, dict[str, Any]], Any]


@dataclass(frozen=True)
class RelationSpec:
    name: str
    source_kinds: tuple[str, ...]
    target_kinds: tuple[str, ...]
    source_operators: tuple[str, ...] = ()
    target_operators: tuple[str, ...] = ()
    input_ports: tuple[str, ...] = ()
    output_ports: tuple[str, ...] = ("result",)
    validators: tuple[RelationValidator, ...] = ()
    geometry_validators: tuple[RelationValidator, ...] = ()
    geometry_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "source_kinds": list(self.source_kinds),
                "target_kinds": list(self.target_kinds),
                "source_operators": list(self.source_operators),
                "target_operators": list(self.target_operators),
                "input_ports": list(self.input_ports), "output_ports": list(self.output_ports),
                "validators": [getattr(check, "__name__", type(check).__name__) for check in self.validators],
                "geometry_validators": [getattr(check, "__name__", type(check).__name__)
                                        for check in self.geometry_validators],
                "geometry_required": self.geometry_required}


class RelationRegistry:
    def __init__(self) -> None:
        self.specs: dict[str, RelationSpec] = {}

    def register(self, spec: RelationSpec, replace: bool = False) -> RelationSpec:
        if not isinstance(spec, RelationSpec) or not isinstance(spec.name, str) or not spec.name.strip():
            raise ValueError("Register a RelationSpec with a non-empty name")
        for field in ("source_kinds", "target_kinds", "source_operators", "target_operators",
                      "input_ports", "output_ports"):
            values = getattr(spec, field)
            if isinstance(values, str) or not isinstance(values, (tuple, list)) or any(
                    not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"Relation {field} must contain non-empty strings")
        if not spec.source_kinds or not spec.target_kinds or not spec.output_ports:
            raise ValueError("Relation needs source/target kinds and output ports")
        if not spec.validators or any(not callable(check) for check in spec.validators):
            raise ValueError("Relation needs at least one callable semantic validator")
        if any(not callable(check) for check in spec.geometry_validators):
            raise ValueError("Relation geometry validators must be callable")
        if not isinstance(spec.geometry_required, bool):
            raise ValueError("Relation geometry_required must be a boolean")
        if spec.geometry_required and not spec.geometry_validators:
            raise ValueError("Physical relation needs a geometry validator")
        if spec.name in self.specs and not replace:
            raise ValueError(f"Relation already registered: {spec.name}")
        self.specs[spec.name] = spec
        return spec

    def get(self, name: str) -> RelationSpec | None:
        return self.specs.get(name)

    @staticmethod
    def _run(checks, edge, source, target, context):
        evidence = []
        for check in checks:
            name = getattr(check, "__name__", type(check).__name__)
            try:
                value = check(edge, source, target, context)
                if value is None:
                    report = {"passed": True}
                elif isinstance(value, bool):
                    report = {"passed": value}
                elif isinstance(value, dict) and isinstance(value.get("passed"), bool):
                    report = deepcopy(value)
                else:
                    raise ValueError("Validator must return None, bool, or a passed report")
                report = {"name": name, **report}
                json.dumps(report, allow_nan=False)
            except Exception as error:
                report = {"name": name, "passed": False, "reason": str(error),
                          "error_type": type(error).__name__}
            evidence.append(report)
        return evidence

    def validate_contract(self, edge: Dependency, source: Node, target: Node,
                          context: dict[str, Any]) -> list[dict[str, Any]]:
        spec = self.get(edge.relation)
        if spec is None:
            return [{"name": "registered_relation", "passed": False,
                     "reason": "Unknown semantic relation retained as a hypothesis"}]
        reason = None
        for label, value, allowed in (
                ("source kind", source.kind, spec.source_kinds),
                ("target kind", target.kind, spec.target_kinds),
                ("source operator", source.operator, spec.source_operators),
                ("target operator", target.operator, spec.target_operators),
                ("input port", context["input_port"], spec.input_ports),
                ("output port", context["output_port"], spec.output_ports)):
            result_alias = label == "output port" and value == source.kind and "result" in allowed
            if allowed and value not in allowed and "*" not in allowed and not result_alias:
                reason = f"Relation {spec.name} does not accept {label}: {value}"
                break
        evidence = [{"name": "relation_contract", "passed": reason is None,
                     "relation": spec.name, "source_id": source.id, "target_id": target.id,
                     "input_port": context["input_port"], "output_port": context["output_port"],
                     **({"reason": reason} if reason else {})}]
        if reason is None:
            evidence.extend(self._run(spec.validators, edge, source, target, context))
        return evidence

    def validate_geometry(self, edge: Dependency, source: Node, target: Node,
                          context: dict[str, Any]) -> list[dict[str, Any]]:
        spec = self.get(edge.relation)
        if spec is None or (spec.geometry_required and not spec.geometry_validators):
            return [{"name": "relation_geometry", "passed": False,
                     "reason": "Required relation geometry validator is unavailable"}]
        return self._run(spec.geometry_validators, edge, source, target, context)


def _program_binding(edge, source, target, context):
    analytic_consumers = {"road.polyline", "vegetation.scatter", "building.proxy", "blender.terrain"}
    mesh_consumers = {"blender.road", "blender.grass", "blender.object", "external.infinigen.grass"}
    if source.kind == "terrain":
        if target.operator in analytic_consumers and source.operator != "terrain.heightfield":
            return {"passed": False, "reason": "Terrain representation is not an analytic heightfield for this consumer"}
        if target.operator in mesh_consumers and source.operator not in {"blender.terrain", "external.moge.mesh"}:
            return {"passed": False, "reason": "Terrain representation has no native support mesh for this consumer"}
    return {"passed": True, "source_id": source.id, "target_id": target.id,
            "input_port": context["input_port"],
            "reason": "Registered relation kind, operator and port contract matched"}


def _postcondition_evidence(context):
    def flatten(value):
        if not isinstance(value, dict):
            return
        yield value
        for item in value.get("checks", []):
            yield from flatten(item)
    for report in context["node_reports"]:
        if report.get("node_id") == context["target_id"]:
            for value in report.get("postconditions", {}).get("evidence", []):
                yield from flatten(value)


def _backend_geometry(edge, source, target, context):
    if edge.relation == "road_exclusion_buffer":
        rule = "actual_grass_road_clearance"
    else:
        rule = {"terrain": "actual_heightfield_surface", "road": "actual_road_mesh_contact",
                "vegetation": "actual_native_grass_contact", "building": "actual_object_bottom_contact"}[target.kind]
    checks = [item for item in _postcondition_evidence(context)
              if item.get("rule") == rule and item.get("node_id") == target.id
              and item.get("source_id") == source.id and isinstance(item.get("passed"), bool)]
    return {"passed": bool(checks) and all(check["passed"] for check in checks), "rule": rule,
            "source_id": source.id, "target_id": target.id, "checks": checks,
            "reason": "Exact dependency endpoint geometry checked" if checks else
                      "Missing geometry evidence for the exact source and target of this relation"}


def _proxy_geometry(edge, source, target, context):
    """Reuse existing constraint calculations against the actual relation source."""
    from outdoorgraph.constraints import evaluate_constraints

    scene = context["scene"]
    artifacts = context["artifacts"]
    if edge.relation == "road_exclusion_buffer":
        rule = "vegetation_road_clearance"
        params = {"buffer_m": target.params.get("road_buffer_m", 2)}
    elif target.kind == "road":
        rule, params = "road_contact", {}
    else:
        rule, params = "ground_contact", {}
    nodes = [target.id, source.id]
    for constraint in scene.constraints:
        if constraint.hard and constraint.rule == rule and constraint.node_ids == nodes:
            params = {**params, **constraint.params}
    subset = copy(scene)
    subset.constraints = [Constraint("relation:"+source.id+":"+target.id, rule, nodes, params)]
    if target.kind != "vegetation" or rule != "ground_contact":
        checks = evaluate_constraints(subset, artifacts)
    else:
        positions = artifacts[target.id]["positions"]
        checks = []
        for index, position in enumerate(positions):
            sample = dict(artifacts)
            sample[target.id] = {"position_xyz": position}
            check = evaluate_constraints(subset, sample)[0]
            checks.append({**check, "sample_index": index})
    return {"passed": all(check["passed"] for check in checks), "rule": rule,
            "source_id": source.id, "target_id": target.id, "sample_count": len(checks),
            "checks": checks, "reason": "Relation geometry evaluated against its declared source"}


def _physical_geometry(edge, source, target, context):
    # Native geometry must be inspected by its registered backend. Proxy height
    # fields cannot stand in for a mesh or certify generated GN instances.
    if target.operator in {"blender.terrain", "blender.road", "blender.grass", "blender.object",
                           "external.infinigen.grass"}:
        return _backend_geometry(edge, source, target, context)
    return _proxy_geometry(edge, source, target, context)


def default_relation_registry() -> RelationRegistry:
    registry = RelationRegistry()
    surface_ops = ("terrain.heightfield", "blender.terrain", "external.moge.mesh")
    definitions = (
        ("samples_terrain_height", ("terrain",), ("terrain", "road", "vegetation"), surface_ops,
         ("blender.terrain", "road.polyline", "vegetation.scatter", "blender.road", "blender.grass"),
         ("heightfield", "terrain")),
        ("supported_by_terrain", ("terrain",), ("building", "vegetation"), surface_ops,
         ("building.proxy", "blender.object", "vegetation.scatter", "blender.grass", "external.infinigen.grass"),
         ("terrain",)),
        ("road_exclusion_buffer", ("road",), ("vegetation",), ("road.polyline", "blender.road"),
         ("vegetation.scatter", "blender.grass", "external.infinigen.grass"), ("roads",)),
        ("scatter_on_reconstructed_surface", ("terrain",), ("vegetation",), ("external.moge.mesh",),
         ("external.infinigen.grass",), ("terrain",)),
        ("heightfield_to_surface", ("terrain",), ("terrain",), ("terrain.heightfield",),
         ("blender.terrain",), ("heightfield",)),
    )
    for name, source_kinds, target_kinds, source_ops, target_ops, input_ports in definitions:
        registry.register(RelationSpec(name, source_kinds, target_kinds, source_ops, target_ops, input_ports,
            ("result", *source_kinds), (_program_binding,), (_physical_geometry,), True))
    return registry

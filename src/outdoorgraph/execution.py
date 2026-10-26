"""Typed planning, lifecycle gates and incremental execution for every backend.

No backend is selected from arbitrary code in Scene IR. Callers explicitly
register trusted callables. Confidence remains observational metadata and never
grants execution permission. Cache signatures are content descriptions, not a
security mechanism or file-integrity checker.
"""

from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import math
import time
from typing import Any, Callable

from outdoorgraph.constraints import evaluate_constraints
from outdoorgraph.external_inputs import resolve_external_inputs
from outdoorgraph.ir import Node, Scene
from outdoorgraph.operators import REGISTRY
from outdoorgraph.relations import RelationRegistry, RelationSpec, default_relation_registry
from outdoorgraph.runtime import RunResult, downstream
from outdoorgraph.validation import topological_ids, validate_scene

Artifact = dict[str, Any]
RunCallable = Callable[[Node, dict[str, Any], dict[str, Any]], Artifact]



class ContractValidationError(ValueError):
    pass


@dataclass(frozen=True)
class InputSlot:
    kinds: tuple[str, ...]
    required: bool = True
    multiple: bool = False
    relations: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.kinds, str):
            object.__setattr__(self, "kinds", (self.kinds,))
        if not self.kinds or any(not isinstance(kind, str) or not kind for kind in self.kinds):
            raise ValueError("InputSlot needs non-empty artifact kinds")


@dataclass(frozen=True)
class OperatorSpec:
    name: str
    run: RunCallable
    input_slots: dict[str, InputSlot]
    output_kinds: tuple[str, ...]
    version: str = "1"
    backend: str = "proxy"
    preconditions: tuple[Callable, ...] = ()
    postconditions: tuple[Callable, ...] = ()
    cache_validator: Callable | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    external_inputs: Callable | dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "backend": self.backend,
                "input_slots": {name: {"kinds": list(slot.kinds), "required": slot.required,
                                       "multiple": slot.multiple, "relations": slot.relations}
                                for name, slot in self.input_slots.items()},
                "output_kinds": list(self.output_kinds), "parameters": deepcopy(self.parameters),
                "seed_source": "Node.seed", "dependency_source": "Scene.dependencies",
                "preconditions": [getattr(check, "__name__", type(check).__name__) for check in self.preconditions],
                "postconditions": [getattr(check, "__name__", type(check).__name__) for check in self.postconditions],
                "external_inputs": (getattr(self.external_inputs, "__name__", type(self.external_inputs).__name__)
                                    if callable(self.external_inputs) else
                                    {name: str(path) for name, path in (self.external_inputs or {}).items()})}


class OperatorRegistry:
    def __init__(self, relations: RelationRegistry | None = None) -> None:
        self.specs: dict[str, OperatorSpec] = {}
        self.relations = relations if relations is not None else default_relation_registry()

    def register_relation(self, spec: RelationSpec, replace: bool = False) -> RelationSpec:
        return self.relations.register(spec, replace=replace)

    def register(self, spec: OperatorSpec, replace: bool = False) -> OperatorSpec:
        if not isinstance(spec, OperatorSpec) or not callable(spec.run):
            raise ValueError("Register an OperatorSpec with a callable run function")
        if any(not isinstance(value, str) or not value.strip()
               for value in (spec.name, spec.version, spec.backend)):
            raise ValueError("Operator name, version and backend must be non-empty strings")
        if not spec.output_kinds or any(not isinstance(kind, str) or not kind for kind in spec.output_kinds):
            raise ValueError("Operator needs declared output kinds")
        if any(not isinstance(name, str) or not name or not isinstance(slot, InputSlot)
               for name, slot in spec.input_slots.items()):
            raise ValueError("Operator input_slots must map names to InputSlot")
        if spec.name in self.specs and not replace:
            raise ValueError(f"Operator already registered: {spec.name}")
        self.specs[spec.name] = spec
        return spec

    def get(self, name: str) -> OperatorSpec | None:
        return self.specs.get(name)


def proxy_registry() -> OperatorRegistry:
    registry = OperatorRegistry()
    declarations = {
        "terrain.heightfield": ({}, ("terrain",)),
        "road.polyline": ({"terrain": InputSlot(("terrain",))}, ("road",)),
        "vegetation.scatter": ({"terrain": InputSlot(("terrain",)),
                                "roads": InputSlot(("road",), required=False, multiple=True)}, ("vegetation",)),
        "building.proxy": ({"terrain": InputSlot(("terrain",))}, ("building",)),
        "background.constant": ({}, ("background",)),
    }
    for name, (slots, outputs) in declarations.items():
        def run(node, inputs, context, operator_name=name):
            flat = {}
            for port, source_ids in context["input_bindings"].items():
                values = inputs[port] if isinstance(inputs[port], list) else [inputs[port]]
                flat.update(zip(source_ids, values))
            return REGISTRY[operator_name](node, flat)
        checks = (_road_parameters,) if name == "road.polyline" else ()
        registry.register(OperatorSpec(name, run, slots, outputs, preconditions=checks))
    return registry


def _road_parameters(node: Node, inputs: dict[str, Any], context: dict[str, Any]) -> None:
    """Admission checks precede signature encoding, preserving useful errors."""
    from outdoorgraph.operators import finite_number

    width = float(node.params.get("width_m", 4))
    if not math.isfinite(width) or width <= 0:
        raise ValueError("Road width_m must be finite and positive")
    finite_number(node.params.get("height_offset_m", 0), "height_offset_m")
    spacing = finite_number(node.params.get("sample_spacing_m", 1), "sample_spacing_m")
    if spacing <= 0:
        raise ValueError("sample_spacing_m must be positive")


_DEFAULT_REGISTRY: OperatorRegistry | None = None


def default_registry() -> OperatorRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = proxy_registry()
    return _DEFAULT_REGISTRY


def register_operator(spec: OperatorSpec, registry: OperatorRegistry | None = None,
                      replace: bool = False) -> OperatorSpec:
    return (registry if registry is not None else default_registry()).register(spec, replace)


register = register_operator


@dataclass
class Plan:
    order: list[str]
    bindings: dict[str, dict[str, list[str]]]
    dependency_reports: list[dict[str, Any]]
    errors: list[dict[str, Any]] = field(default_factory=list)
    relations: RelationRegistry | None = field(default=None, repr=False)

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {"order": self.order, "bindings": self.bindings,
                "dependency_reports": self.dependency_reports, "errors": self.errors,
                "passed": self.passed}


@dataclass
class DependencyValidation:
    scene: Scene
    dependency_reports: list[dict[str, Any]]
    errors: list[dict[str, Any]]

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {"scene": self.scene.to_dict(), "dependency_reports": self.dependency_reports,
                "errors": self.errors, "passed": self.passed}


class Planner:
    def __init__(self, scene: Scene, registry: OperatorRegistry | None = None):
        self.scene = scene
        self.registry = registry if registry is not None else default_registry()

    def validate_dependencies(self, only_proposed: bool = True) -> DependencyValidation:
        """Explicitly validate hypotheses; runtime geometry gates remain pending.

        Serialized validated/executable declarations are never verification
        evidence. Every candidate is checked against its current semantic
        registry, operator contract, ports and graph topology on every plan.
        """
        candidate = deepcopy(self.scene)
        eligible = set()
        for index, edge in enumerate(candidate.dependencies):
            if edge.status == "rejected" or (only_proposed and edge.status != "proposed"):
                continue
            eligible.add(index)
            edge.status = "validated"
        try:
            plan = Planner(candidate, self.registry).plan()
        except Exception as error:
            return DependencyValidation(deepcopy(self.scene), [],
                                        [{"phase": "contract", "reason": str(error),
                                          "error_type": type(error).__name__}])
        failed_targets = {item.get("node_id") for item in plan.errors}
        for report in plan.dependency_reports:
            index = report["index"]
            report["declared_status"] = self.scene.dependencies[index].status
            edge = candidate.dependencies[index]
            if index not in eligible:
                # Legacy declarations are still rechecked, even if the caller
                # only requests migration of proposed edges.
                if report["status"] == "proposed":
                    edge.status = "proposed"
                elif report["status"] == "contract_rejected":
                    edge.status = "rejected"
                elif report["status"] == "contract_validated":
                    edge.status = "validated"
                continue
            if report["status"] == "contract_validated" and edge.target_id not in failed_targets:
                edge.status = "validated"
                report.update(status="validated", lifecycle="validated", geometry_status="pending",
                              reason="Semantic and typed contracts validated; runtime geometry gates remain pending")
            elif report["status"] == "contract_rejected":
                edge.status = "rejected"
                report.update(lifecycle="rejected", geometry_status="not_checked")
            else:
                edge.status = "proposed"
                report.update(status="proposed", lifecycle="proposed", geometry_status="not_checked")
        return DependencyValidation(candidate, plan.dependency_reports, plan.errors)

    def plan(self) -> Plan:
        validate_scene(self.scene, check_cycles=False)
        order = [node.id for node in self.scene.nodes]
        nodes = {node.id: node for node in self.scene.nodes}
        bindings = {node_id: {} for node_id in order}
        reports, errors = [], []
        for node in self.scene.nodes:
            spec = self.registry.get(node.operator)
            if spec is None:
                errors.append({"node_id": node.id, "phase": "contract",
                               "reason": f"Unknown procedural operator: {node.operator}"})
            elif node.kind not in spec.output_kinds and "*" not in spec.output_kinds:
                errors.append({"node_id": node.id, "phase": "contract",
                               "reason": f"Declared node kind {node.kind} is not an output of {spec.name}"})
        for index, edge in enumerate(self.scene.dependencies):
            state = edge.status if edge.status in {"proposed", "rejected"} else "contract_validated"
            report = {"index": index, "source_id": edge.source_id, "target_id": edge.target_id,
                      "relation": edge.relation, "confidence": edge.confidence,
                      "declared_status": edge.status, "input_port": edge.input_port,
                      "output_port": edge.output_port or "result", "status": state,
                      "lifecycle": edge.status if state != "contract_validated" else "validated",
                      "semantic_evidence": [], "geometry_evidence": [],
                      "geometry_status": "not_applicable" if state in {"proposed", "rejected"} else "pending"}
            reports.append(report)
            if state in {"proposed", "rejected"}:
                report["reason"] = "Hypothesis or rejected relation is not an execution dependency"
                continue
            source, target = nodes[edge.source_id], nodes[edge.target_id]
            spec = self.registry.get(target.operator)
            source_spec = self.registry.get(source.operator)
            relation = self.registry.relations.get(edge.relation)
            reason = None
            if relation is None:
                report.update(status="proposed", lifecycle="proposed", geometry_status="not_applicable",
                              reason="Unknown semantic relation retained as a hypothesis; no registered validator")
                continue
            if source_spec is None or spec is None:
                reason = "Dependency operator has no registered contract"
            elif source.kind not in source_spec.output_kinds and "*" not in source_spec.output_kinds:
                reason = f"Source kind {source.kind} is not declared by {source_spec.name}"
            elif edge.output_port not in (None, "result", source.kind):
                reason = f"Unknown source output port: {edge.output_port}"
            else:
                choices = [name for name, slot in spec.input_slots.items()
                           if (source.kind in slot.kinds or "*" in slot.kinds)
                           and (slot.relations is None or edge.relation in slot.relations)]
                port = edge.input_port
                if port is None:
                    if len(choices) != 1:
                        reason = f"Cannot uniquely bind {source.kind} to an input of {target.id}"
                    else:
                        port = choices[0]
                elif port not in spec.input_slots:
                    reason = f"Unknown input port: {port}"
                elif port not in choices:
                    reason = f"Input port {port} does not accept {source.kind} or relation {edge.relation}"
                if reason is None:
                    evidence = self.registry.relations.validate_contract(
                        edge, source, target, {"scene": self.scene, "input_port": port,
                                              "output_port": report["output_port"]})
                    report["semantic_evidence"] = evidence
                    failed = [check for check in evidence if not check["passed"]]
                    if failed:
                        reason = failed[0].get("reason", "Semantic relation validator failed")
                    else:
                        slot = spec.input_slots[port]
                        existing = bindings[target.id].setdefault(port, [])
                        if source.id in existing or (existing and not slot.multiple):
                            reason = f"Duplicate dependency binding on {target.id}.{port}"
                        else:
                            existing.append(source.id)
                            report["input_port"] = port
            if reason:
                report.update(status="contract_rejected", lifecycle="rejected",
                              geometry_status="not_checked", reason=reason)
                errors.append({"node_id": target.id, "phase": "contract", "reason": reason,
                               "dependency_index": index, "semantic_evidence": report["semantic_evidence"]})
            else:
                report["reason"] = "Semantic and structural contracts passed; execution and geometry gates are pending"
        for node in self.scene.nodes:
            spec = self.registry.get(node.operator)
            if spec is not None:
                for port, slot in spec.input_slots.items():
                    if slot.required and not bindings[node.id].get(port):
                        errors.append({"node_id": node.id, "phase": "contract",
                                       "reason": f"Missing required input: {node.id}.{port}"})
        executable_graph = copy(self.scene)
        executable_graph.dependencies = [self.scene.dependencies[report["index"]] for report in reports
                                         if report["status"] == "contract_validated"]
        order = topological_ids(executable_graph)
        return Plan(order, bindings, reports, errors, self.registry.relations)

def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def node_signature(node: Node, spec: OperatorSpec, bindings: dict[str, list[str]],
                   artifacts: dict[str, Artifact], dependency_reports: list[dict[str, Any]],
                   external_inputs: dict[str, Any] | None = None) -> str:
    dependencies = []
    for report in dependency_reports:
        if report["target_id"] == node.id and report["status"] == "contract_validated":
            source_id = report["source_id"]
            dependencies.append({"source_id": source_id, "input_port": report["input_port"],
                                 "output_port": report["output_port"],
                                 "relation": report["relation"],
                                 "result_id": artifacts[source_id]["_execution"]["result_id"]})
    dependencies.sort(key=lambda item: (item["input_port"], item["source_id"], item["output_port"]))
    payload = {"node_id": node.id, "kind": node.kind, "operator": spec.name,
                    "version": spec.version, "backend": spec.backend, "params": node.params,
                    "seed": node.seed, "bounds_xy": node.bounds_xy, "metadata": node.metadata,
                    "evidence_ids": node.evidence_ids, "input_bindings": bindings,
                    "dependencies": dependencies}
    if external_inputs:
        payload["external_inputs"] = external_inputs
    return _digest(payload)


def _check_output(node: Node, spec: OperatorSpec, artifact: Artifact) -> None:
    if not isinstance(artifact, dict) or artifact.get("kind") != node.kind:
        raise ContractValidationError(f"Output kind does not match {node.id}:{node.kind}")
    if artifact["kind"] not in spec.output_kinds and "*" not in spec.output_kinds:
        raise ContractValidationError(f"Undeclared output kind from {spec.name}")
    _canonical(artifact)


def _ready_checks(scene: Scene, artifacts: dict[str, Artifact]) -> list[dict[str, Any]]:
    subset = copy(scene)
    subset.constraints = [item for item in scene.constraints if all(node_id in artifacts for node_id in item.node_ids)]
    return evaluate_constraints(subset, artifacts)


def _predicate(check: Callable, args: tuple, label: str) -> dict[str, Any]:
    value = check(*args)
    name = getattr(check, "__name__", type(check).__name__)
    if value is None:
        report = {"name": name, "passed": True}
    elif isinstance(value, bool):
        report = {"name": name, "passed": value}
    elif isinstance(value, dict) and isinstance(value.get("passed"), bool):
        report = {"name": name, **value}
    else:
        raise ContractValidationError(f"{label} {name} must return None, bool, or a passed report")
    _canonical(report)
    if not report["passed"]:
        error = ContractValidationError(f"{label} failed: {name}: {report.get('reason', '')}")
        error.check_evidence = report
        raise error
    return report


def _check_relation_geometry(result: RunResult, scene: Scene, plan: Plan,
                             target_id: str | None = None) -> bool:
    """Gate newly generated inputs before any downstream callable consumes them.

    Evidence belongs to the current plan, never to a trusted serialized status.
    The finish pass calls this again for any checks not ready during generation.
    """
    passed = True
    for report in plan.dependency_reports:
        if report["status"] != "contract_validated" or (target_id is not None and report["target_id"] != target_id):
            continue
        if not report.get("geometry_checked"):
            edge = scene.dependencies[report["index"]]
            if edge.source_id not in result.artifacts or edge.target_id not in result.artifacts:
                continue
            evidence = plan.relations.validate_geometry(
                edge, scene.get_node(edge.source_id), scene.get_node(edge.target_id),
                {"scene": scene, "artifacts": result.artifacts, "node_reports": result.node_reports,
                 "constraint_reports": result.constraint_reports, "source_id": edge.source_id,
                 "target_id": edge.target_id, "input_port": report["input_port"],
                 "output_port": report["output_port"]})
            report.update(geometry_evidence=evidence, geometry_checked=True)
            failed = [check for check in evidence if not check["passed"]]
            if failed:
                failure = {"node_id": edge.target_id, "phase": "relation_geometry",
                           "dependency_index": report["index"], "relation": edge.relation,
                           "reason": failed[0].get("reason", "Required relation geometry failed"),
                           "check_evidence": deepcopy(evidence)}
                result.failures.append(failure)
                for node_report in result.node_reports:
                    if node_report.get("node_id") == edge.target_id:
                        node_report.update(status="failed", phase="relation_geometry", reason=failure["reason"],
                                           relation_evidence=deepcopy(evidence))
        if any(not check["passed"] for check in report["geometry_evidence"]):
            passed = False
    return passed


def _finish(result: RunResult, scene: Scene, plan: Plan | None) -> RunResult:
    result.scene = deepcopy(scene)
    followers = {node.id: set() for node in scene.nodes}
    if plan is not None:
        _check_relation_geometry(result, scene, plan)
        result.dependency_reports = deepcopy(plan.dependency_reports)
        for target, ports in plan.bindings.items():
            for sources in ports.values():
                for source in sources:
                    followers[source].add(target)

    def affected(start):
        closure = set(start)
        pending = list(closure)
        while pending:
            for target in followers.get(pending.pop(), ()):
                if target not in closure:
                    closure.add(target)
                    pending.append(target)
        return closure

    failed_constraint_scopes = {check["id"]: affected(check["nodes"])
                                for check in result.constraint_reports if check["hard"] and not check["passed"]}
    failed_nodes = {node_id for check in result.constraint_reports
                    if check["hard"] and not check["passed"] for node_id in check["nodes"]}
    failed_nodes.update(failure["node_id"] for failure in result.failures if "node_id" in failure)
    # Late-ready checks can fail after descendants were generated. Artifacts
    # remain auditable, but failed descendants cannot become valid result cache.
    failed_nodes = affected(failed_nodes)
    for node_id, artifact in result.artifacts.items():
        artifact["_execution"]["validation_status"] = "failed" if node_id in failed_nodes else "passed"
        artifact["_execution"]["constraint_check_ids"] = [check["id"] for check in result.constraint_reports
                                                          if node_id in check["nodes"]
                                                          or node_id in failed_constraint_scopes.get(check["id"], ())]
    if plan is not None:
        for report in result.dependency_reports:
            edge = result.scene.dependencies[report["index"]]
            if report["status"] in {"proposed", "rejected"}:
                edge.status = report["status"]
                continue
            if report["status"] == "contract_rejected":
                edge.status = "rejected"
                continue
            ready = (report["source_id"] in result.artifacts and report["target_id"] in result.artifacts
                     and not failed_nodes.intersection((report["source_id"], report["target_id"])))
            endpoints = {report["source_id"], report["target_id"]}
            checks = [check for check in result.constraint_reports if check["hard"]
                      and (set(check["nodes"]).intersection(endpoints)
                           or failed_constraint_scopes.get(check["id"], set()).intersection(endpoints))]
            report["check_ids"] = [check["id"] for check in checks]
            report["constraint_evidence"] = [deepcopy(check) for check in checks]
            failed_execution = any(failure.get("node_id") in (None, report["source_id"], report["target_id"])
                                   for failure in result.failures)
            relation = plan.relations.get(edge.relation)
            geometry_evidence = report["geometry_evidence"]
            geometry_passed = all(check["passed"] for check in geometry_evidence)
            if relation.geometry_required and not geometry_evidence:
                geometry_passed = False
            if ready and not failed_execution and geometry_passed:
                report.update(status="executable", lifecycle="executable",
                              geometry_status="passed" if geometry_evidence or checks else "not_required",
                              reason="Semantic contract, execution, postconditions and required geometry gates passed")
                edge.status = "executable"
            else:
                report.update(status="validated", lifecycle="validated", geometry_status="failed_or_blocked",
                              blocked_by=sorted(failed_nodes.intersection(endpoints)),
                              reason="Validated contract is blocked by missing or failed execution/geometry evidence")
                edge.status = "validated"
    if result.failures or any(check["hard"] and not check["passed"] for check in result.constraint_reports):
        result.status = "failed"
    return result

def execute(scene: Scene, previous: dict[str, Artifact] | RunResult | None = None,
            changed_ids: set[str] | None = None, registry: OperatorRegistry | None = None,
            context: dict[str, Any] | None = None, raise_on_error: bool = False) -> RunResult:
    """Run one graph; signatures infer edits even without caller dirty-node hints."""
    result = RunResult({}, [], [], [])
    registry = registry if registry is not None else default_registry()
    plan = None
    try:
        plan = Planner(scene, registry).plan()
        if plan.errors:
            result.failures.extend(plan.errors)
            raise ContractValidationError(plan.errors[0]["reason"])
        admitted_graph = copy(scene)
        admitted_graph.dependencies = [scene.dependencies[report["index"]] for report in plan.dependency_reports
                                       if report["status"] == "contract_validated"]
        forced = downstream(admitted_graph, set(changed_ids or ()))
    except Exception as error:
        if not result.failures:
            result.failures.append({"phase": "planning", "reason": str(error), "error_type": type(error).__name__})
        _finish(result, scene, plan)
        if raise_on_error:
            raise
        return result
    old = previous.artifacts if isinstance(previous, RunResult) else (previous or {})
    blocked = set()
    for node_id in plan.order:
        node = scene.get_node(node_id)
        spec = registry.get(node.operator)
        bindings = plan.bindings[node_id]
        source_ids = [item for values in bindings.values() for item in values]
        if any(source_id not in result.artifacts or source_id in blocked for source_id in source_ids):
            blocked.add(node_id)
            result.node_reports.append({"node_id": node_id, "status": "blocked", "phase": "input",
                                        "reason": "Upstream execution or hard constraint failed"})
            continue
        phase = "precondition"
        started = time.monotonic()
        try:
            local_context = dict(context or {})
            local_context.update(node_id=node_id, input_bindings=deepcopy(bindings))
            inputs = {}
            for port, slot in spec.input_slots.items():
                values = [result.artifacts[source_id] for source_id in bindings.get(port, [])]
                if slot.multiple:
                    inputs[port] = values
                elif values:
                    inputs[port] = values[0]
            phase = "precondition"
            pre_reports = [_predicate(check, (node, inputs, local_context), "Precondition")
                           for check in spec.preconditions]
            phase = "external_inputs"
            declarations = (spec.external_inputs(node, inputs, local_context)
                            if callable(spec.external_inputs) else (spec.external_inputs or {}))
            paths, identities = resolve_external_inputs(declarations, local_context.get("external_input_base_dir"))
            local_context.update(external_input_paths=paths, external_input_identities=identities)
            phase = "signature"
            signature = node_signature(node, spec, bindings, result.artifacts, plan.dependency_reports, identities)
            local_context["signature"] = signature
            candidate = old.get(node_id)
            cached_metadata = candidate.get("_execution") if isinstance(candidate, dict) else None
            reason = "cache_missing"
            reuse = False
            if isinstance(cached_metadata, dict) and cached_metadata.get("signature") == signature:
                reason = "signature_match"
                reuse = True
                try:
                    _check_output(node, spec, candidate)
                    if (not isinstance(cached_metadata.get("result_id"), str)
                            or not cached_metadata["result_id"]):
                        reuse, reason = False, "cache_metadata_missing"
                    elif candidate["_execution"].get("validation_status") == "failed":
                        reuse, reason = False, "cache_previously_failed"
                    elif spec.cache_validator and not spec.cache_validator(candidate, local_context):
                        reuse, reason = False, "cache_resource_missing"
                except Exception:
                    reuse, reason = False, "cache_invalid"
            elif candidate is not None:
                reason = "signature_changed_or_unsigned"
            if node_id in forced:
                reuse, reason = False, "explicit_edit"
            elif any(source_id in result.rebuilt_ids for source_id in source_ids):
                reuse, reason = False, "upstream_rebuilt"
            phase = "execution"
            artifact = deepcopy(candidate) if reuse else spec.run(node, inputs, local_context)
            phase = "postcondition"
            _check_output(node, spec, artifact)
            post_reports = [_predicate(check, (node, artifact, inputs, local_context), "Postcondition")
                            for check in spec.postconditions]
            if paths:
                phase = "external_inputs"
                _, after_identities = resolve_external_inputs(paths)
                if after_identities != identities:
                    raise ValueError("External inputs changed during node execution: "+node_id)
            if not reuse:
                artifact = deepcopy(artifact)
                artifact.pop("_execution", None)
                result_id = _digest({"signature": signature, "artifact": artifact})
                artifact["_execution"] = {"signature": signature, "result_id": result_id,
                                           "node_id": node_id, "operator": spec.name,
                                           "version": spec.version, "backend": spec.backend}
                if identities:
                    artifact["_execution"]["external_inputs"] = deepcopy(identities)
            result.artifacts[node_id] = artifact
            (result.preserved_ids if reuse else result.rebuilt_ids).append(node_id)
            result.node_reports.append({"node_id": node_id, "operator": spec.name,
                                        "backend": spec.backend, "status": "preserved" if reuse else "rebuilt",
                                        "phase": "completed", "reason": reason, "signature": signature,
                                        "result_id": artifact["_execution"]["result_id"],
                                        "preconditions": {"registered": len(spec.preconditions), "passed": True,
                                                          "evidence": pre_reports},
                                        "postconditions": {"registered": len(spec.postconditions),
                                                           "output_contract": "passed", "passed": True,
                                                           "evidence": post_reports},
                                        "seconds": time.monotonic() - started})
            phase = "relation_geometry"
            if not _check_relation_geometry(result, scene, plan, node_id):
                blocked.add(node_id)
            phase = "constraint"
            result.constraint_reports = _ready_checks(scene, result.artifacts)
            blocked.update(node_id for check in result.constraint_reports if check["hard"] and not check["passed"]
                           for node_id in check["nodes"])
        except Exception as error:
            blocked.add(node_id)
            failure = {"node_id": node_id, "phase": phase, "reason": str(error),
                       "error_type": type(error).__name__}
            if hasattr(error, "check_evidence"):
                failure["check_evidence"] = deepcopy(error.check_evidence)
            result.failures.append(failure)
            failed_report = {"status": "failed", **failure, "seconds": time.monotonic() - started}
            if result.node_reports and result.node_reports[-1]["node_id"] == node_id:
                result.node_reports[-1].update(failed_report)
            else:
                result.node_reports.append(failed_report)
            if raise_on_error:
                _finish(result, scene, plan)
                raise
    checked_ids = {check["id"] for check in result.constraint_reports}
    for constraint in scene.constraints:
        if constraint.id not in checked_ids:
            result.constraint_reports.append({"id": constraint.id, "rule": constraint.rule,
                                               "nodes": constraint.node_ids, "hard": constraint.hard,
                                               "passed": False, "status": "blocked",
                                               "reason": "Required artifacts were not successfully generated"})
    return _finish(result, scene, plan)


ExecutionResult = RunResult

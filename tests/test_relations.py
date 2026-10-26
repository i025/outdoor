"""Extensible semantic relation contracts; callables are unit-test validators."""

import pytest

from outdoorgraph.execution import InputSlot, OperatorSpec, Planner, execute, proxy_registry
from outdoorgraph.ir import Dependency, Node, Scene


def relation_api():
    from outdoorgraph.relations import RelationRegistry, RelationSpec
    return RelationRegistry, RelationSpec


def valid_contract(edge, source, target, context):
    return {"passed": True, "reason": "Unit program dependency is authorized",
            "input_port": context["input_port"]}


def custom_graph(validator=valid_contract, geometry=(), geometry_required=False):
    _, RelationSpec = relation_api()
    calls = []
    registry = proxy_registry()
    registry.register(OperatorSpec("unit.source", lambda *args: {"kind": "raw", "value": 2}, {}, ("raw",)))
    registry.register(OperatorSpec("unit.target", lambda node, inputs, context: calls.append(node.id) or {
        "kind": "processed", "value": inputs["data"]["value"]},
        {"data": InputSlot(("raw",))}, ("processed",)))
    registry.register_relation(RelationSpec("unit_transforms", ("raw",), ("processed",),
        source_operators=("unit.source",), target_operators=("unit.target",), input_ports=("data",),
        validators=(validator,), geometry_validators=geometry, geometry_required=geometry_required))
    scene = Scene("custom_relation", [Node("a", "raw", "unit.source"),
                                      Node("b", "processed", "unit.target")],
                  [Dependency("a", "b", "unit_transforms", status="proposed")])
    return scene, registry, calls


def test_relation_registry_rejects_missing_required_validators():
    RelationRegistry, RelationSpec = relation_api()
    registry = RelationRegistry()
    with pytest.raises(ValueError, match="validator"):
        registry.register(RelationSpec("unit_unchecked", ("raw",), ("processed",)))
    with pytest.raises(ValueError, match="geometry"):
        registry.register(RelationSpec("unit_physical", ("raw",), ("processed",),
            validators=(valid_contract,), geometry_required=True))


def test_registered_nonbuiltin_relation_has_explicit_evidence_and_lifecycle():
    scene, registry, calls = custom_graph()
    validation = Planner(scene, registry).validate_dependencies()
    assert validation.passed and calls == []
    report = validation.dependency_reports[0]
    assert report["semantic_evidence"][0]["passed"]
    assert report["semantic_evidence"][0]["input_port"] == "data"
    assert validation.scene.dependencies[0].status == "validated"
    result = execute(validation.scene, registry=registry)
    assert result.passed and calls == ["b"]
    assert result.scene.dependencies[0].status == "executable"
    assert result.dependency_reports[0]["geometry_status"] == "not_required"


@pytest.mark.parametrize("declared_status", ["validated", "executable"])
def test_legacy_status_cannot_bypass_semantic_validator(declared_status):
    def deny(*args):
        return {"passed": False, "reason": "unit semantic mismatch"}
    scene, registry, calls = custom_graph(deny)
    scene.dependencies[0].status = declared_status
    result = execute(scene, registry=registry)
    assert not result.passed and calls == [] and result.artifacts == {}
    assert result.scene.dependencies[0].status == "rejected"
    report = result.dependency_reports[0]
    assert "unit semantic mismatch" in report["reason"]
    assert any(check["passed"] is False for check in report["semantic_evidence"])


def test_relation_geometry_validator_runs_and_invalidates_cache_on_failure():
    state = {"passed": True}
    def geometry(edge, source, target, context):
        assert context["artifacts"][source.id]["value"] == 2
        return {"passed": state["passed"], "reason": "unit geometry gate"}
    scene, registry, _ = custom_graph(geometry=(geometry,), geometry_required=True)
    validated = Planner(scene, registry).validate_dependencies().scene
    first = execute(validated, registry=registry)
    assert first.passed
    state["passed"] = False
    failed = execute(validated, previous=first, registry=registry)
    assert not failed.passed
    assert failed.scene.dependencies[0].status == "validated"
    assert failed.artifacts["b"]["_execution"]["validation_status"] == "failed"
    retried = execute(validated, previous=failed, registry=registry)
    assert "b" in retried.rebuilt_ids


@pytest.mark.parametrize("field,value", [("input_port", "wrong"), ("output_port", "wrong")])
def test_relation_port_restrictions_are_not_bypassed(field, value):
    scene, registry, _ = custom_graph()
    scene.dependencies[0].status = "validated"
    setattr(scene.dependencies[0], field, value)
    result = execute(scene, registry=registry)
    assert not result.passed and result.artifacts == {}


@pytest.mark.parametrize("target_operator,target_kind,port", [
    ("road.polyline", "road", "terrain"), ("blender.terrain", "terrain", "heightfield")])
def test_mesh_representation_cannot_masquerade_as_analytic_heightfield(target_operator, target_kind, port):
    registry = proxy_registry()
    registry.register(OperatorSpec("external.moge.mesh", lambda *args: {"kind": "terrain", "params": {}},
                                   {}, ("terrain",)))
    if target_operator == "blender.terrain":
        registry.register(OperatorSpec(target_operator, lambda *args: {"kind": "terrain"},
                                       {port: InputSlot(("terrain",))}, ("terrain",)))
    scene = Scene("representation_contract", [Node("mesh", "terrain", "external.moge.mesh"),
                                               Node("consumer", target_kind, target_operator)],
                  [Dependency("mesh", "consumer", "samples_terrain_height", status="validated",
                              input_port=port)])
    plan = Planner(scene, registry).plan()
    assert not plan.passed
    assert plan.dependency_reports[0]["status"] == "contract_rejected"
    assert "representation" in plan.dependency_reports[0]["reason"].lower()

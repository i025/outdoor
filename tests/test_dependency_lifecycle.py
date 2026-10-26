"""Lifecycle regression fixtures use proxy artifacts, never backend evidence."""

from outdoorgraph.execution import InputSlot, OperatorSpec, Planner, execute, proxy_registry
from outdoorgraph.ir import Dependency, Node, Scene


def simple_scene(status=None):
    edge = Dependency("ground", "path", "samples_terrain_height")
    if status is not None:
        edge.status = status
    return Scene("lifecycle_unit", [Node("ground", "terrain", "terrain.heightfield"),
                                    Node("path", "road", "road.polyline",
                                         {"control_points_xy": [[0, 0], [1, 1]]})], [edge])


def test_new_dependency_is_a_hypothesis_by_default():
    assert Dependency("a", "b", "samples_terrain_height").status == "proposed"


def test_missing_legacy_status_stays_proposed_until_explicit_validation():
    value = simple_scene().to_dict()
    value["dependencies"][0].pop("status")
    scene = Scene.from_dict(value)
    assert scene.dependencies[0].status == "proposed"
    blocked = execute(scene)
    assert not blocked.passed and blocked.artifacts == {}
    assert blocked.scene.dependencies[0].status == "proposed"
    validated = Planner(scene).validate_dependencies()
    assert validated.passed
    assert validated.scene.dependencies[0].status == "validated"
    assert scene.dependencies[0].status == "proposed"
    report = validated.dependency_reports[0]
    assert report["declared_status"] == "proposed"
    assert report["semantic_evidence"] and all(c["passed"] for c in report["semantic_evidence"])
    assert report["geometry_status"] == "pending"
    result = execute(validated.scene)
    assert result.passed and result.scene.dependencies[0].status == "executable"
    assert result.dependency_reports[0]["geometry_evidence"]


def test_builtin_relation_name_cannot_authorize_wrong_semantics():
    registry = proxy_registry()
    registry.register(OperatorSpec("unit.road", lambda *args: {"kind": "road"}, {}, ("road",)))
    registry.register(OperatorSpec("unit.green", lambda *args: {"kind": "vegetation"},
                                   {"roads": InputSlot(("road",))}, ("vegetation",)))
    scene = Scene("wrong_semantics", [Node("r", "road", "unit.road"),
                                       Node("v", "vegetation", "unit.green")],
                  [Dependency("r", "v", "samples_terrain_height", status="validated")])
    result = execute(scene, registry=registry)
    assert not result.passed and result.artifacts == {}
    report = result.dependency_reports[0]
    assert report["status"] == "contract_rejected"
    assert "source kind" in report["reason"].lower()


def test_slot_relation_allowlist_is_not_a_semantic_validator():
    registry = proxy_registry()
    registry.register(OperatorSpec("unit.raw", lambda *args: {"kind": "raw"}, {}, ("raw",)))
    registry.register(OperatorSpec("unit.consumer", lambda *args: {"kind": "done"},
                                   {"data": InputSlot(("raw",), relations=("invented_relation",))}, ("done",)))
    scene = Scene("missing_semantic_validator", [Node("a", "raw", "unit.raw"),
                                                 Node("b", "done", "unit.consumer")],
                  [Dependency("a", "b", "invented_relation", status="validated")])
    result = execute(scene, registry=registry)
    assert not result.passed and result.artifacts == {}
    assert result.scene.dependencies[0].status == "proposed"


def test_physical_relation_needs_geometry_even_without_scene_constraint():
    registry = proxy_registry()
    registry.register(OperatorSpec("road.polyline", lambda *args: {
        "kind": "road", "points": [[0, 0, 5], [1, 1, 5]], "width_m": 1},
        {"terrain": InputSlot(("terrain",))}, ("road",)), replace=True)
    result = execute(simple_scene("validated"), registry=registry)
    assert not result.passed
    assert result.scene.dependencies[0].status == "validated"
    report = result.dependency_reports[0]
    assert report["geometry_status"] == "failed_or_blocked"
    assert any(not check["passed"] for check in report["geometry_evidence"])
    assert result.artifacts["path"]["_execution"]["validation_status"] == "failed"
    assert any(item["phase"] == "relation_geometry" for item in result.failures)

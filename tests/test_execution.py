"""Execution-contract and cache unit tests; toy callables are not backend evidence."""

from copy import deepcopy
from dataclasses import replace

import pytest

from outdoorgraph.execution import (
    ContractValidationError, InputSlot, OperatorSpec, Planner, execute, proxy_registry,
)
from outdoorgraph.ir import Constraint, Dependency, Evidence, Node, Scene


@pytest.fixture
def toy_graph():
    calls = []
    registry = proxy_registry()

    def source(node, inputs, context):
        calls.append(node.id)
        return {"kind": "raw", "value": node.params.get("value", 3),
                "provenance": deepcopy(node.metadata)}

    def target(node, inputs, context):
        calls.append(node.id)
        return {"kind": "processed", "value": inputs["source"]["value"] * 2}

    registry.register(OperatorSpec("unit.source", source, {}, ("raw",)))
    registry.register(OperatorSpec("unit.target", target,
                                   {"source": InputSlot(("raw",), relations=("unit_feed", "unit_feed_other"))},
                                   ("processed",)))
    scene = Scene("unit_graph", [Node("input_alpha", "raw", "unit.source", {"value": 3}),
                                  Node("result_beta", "processed", "unit.target"),
                                  Node("independent_sky", "background", "background.constant")],
                  dependencies=[Dependency("input_alpha", "result_beta", "unit_feed", input_port="source")])
    return scene, registry, calls


def test_unified_proxy_initial_and_automatic_no_hint_edit(demo_scene):
    before = execute(demo_scene)
    assert before.passed
    same = execute(demo_scene, previous=before)
    assert same.rebuilt_ids == []
    assert set(same.preserved_ids) == {node.id for node in demo_scene.nodes}
    demo_scene.get_node("terrain_main").params["local_bump"]["amplitude_m"] = 2.5
    after = execute(demo_scene, previous=before.artifacts)
    assert after.passed
    assert set(after.rebuilt_ids) == {"terrain_main", "road_main", "forest_main", "building_main"}
    assert after.preserved_ids == ["background"]
    assert before.artifacts["terrain_main"]["params"]["local_bump"]["amplitude_m"] == 0


def test_signed_cache_preserves_all_without_calling_registered_functions(toy_graph):
    scene, registry, calls = toy_graph
    first = execute(scene, registry=registry)
    calls.clear()
    second = execute(scene, previous=first, registry=registry)
    assert second.passed and calls == []
    assert second.artifacts == first.artifacts
    assert second.rebuilt_ids == []
    assert second.updated_ids == second.rebuilt_ids
    assert second.retained_ids == second.preserved_ids


@pytest.mark.parametrize("field", ["params", "seed", "bounds_xy", "metadata", "evidence_ids"])
def test_signature_covers_node_execution_and_provenance_inputs(toy_graph, field):
    scene, registry, _ = toy_graph
    before = execute(scene, registry=registry)
    node = scene.get_node("input_alpha")
    if field == "params":
        node.params["value"] = 8
    elif field == "seed":
        node.seed += 1
    elif field == "bounds_xy":
        node.bounds_xy = [-1, -1, 2, 2]
    elif field == "metadata":
        node.metadata["source"] = "new evidence"
    else:
        scene.evidence.append(Evidence("new_evidence", "manual", "unit fixture"))
        node.evidence_ids.append("new_evidence")
    after = execute(scene, previous=before, registry=registry)
    assert after.passed
    assert set(after.rebuilt_ids) == {"input_alpha", "result_beta"}
    assert after.preserved_ids == ["independent_sky"]
    assert after.artifacts["input_alpha"]["_execution"]["signature"] != before.artifacts["input_alpha"]["_execution"]["signature"]


@pytest.mark.parametrize("field,value", [("version", "2"), ("backend", "external_unit")])
def test_operator_version_and_backend_invalidate_cache(toy_graph, field, value):
    scene, registry, _ = toy_graph
    before = execute(scene, registry=registry)
    registry.register(replace(registry.get("unit.source"), **{field: value}), replace=True)
    after = execute(scene, previous=before, registry=registry)
    assert set(after.rebuilt_ids) == {"input_alpha", "result_beta"}
    assert after.preserved_ids == ["independent_sky"]


def test_dependency_relation_edit_changes_child_signature_without_dirty_hint(toy_graph):
    scene, registry, _ = toy_graph
    before = execute(scene, registry=registry)
    scene.dependencies[0].relation = "unit_feed_other"
    after = execute(scene, previous=before, registry=registry)
    assert after.passed
    assert after.rebuilt_ids == ["result_beta"]
    assert set(after.preserved_ids) == {"input_alpha", "independent_sky"}


def test_multiple_input_order_changes_signature_and_matches_fresh_execution(toy_graph):
    _, registry, _ = toy_graph
    registry.register(OperatorSpec(
        "unit.join", lambda node, inputs, context: {
            "kind": "processed", "joined": "".join(item["value"] for item in inputs["sources"])},
        {"sources": InputSlot(("raw",), multiple=True, relations=("unit_join",))}, ("processed",)))
    scene = Scene("ordered_inputs", [Node("alpha", "raw", "unit.source", {"value": "a"}),
                                      Node("beta", "raw", "unit.source", {"value": "b"}),
                                      Node("combined", "processed", "unit.join")],
                  [Dependency("alpha", "combined", "unit_join"),
                   Dependency("beta", "combined", "unit_join")])
    before = execute(scene, registry=registry)
    assert before.passed and before.artifacts["combined"]["joined"] == "ab"
    scene.dependencies.reverse()
    cached = execute(scene, previous=before, registry=registry)
    fresh = execute(scene, registry=registry)
    assert cached.passed and fresh.passed
    assert cached.artifacts["combined"]["joined"] == fresh.artifacts["combined"]["joined"] == "ba"
    assert cached.rebuilt_ids == ["combined"]
    assert set(cached.preserved_ids) == {"alpha", "beta"}
    assert cached.artifacts["combined"]["_execution"]["signature"] != before.artifacts["combined"]["_execution"]["signature"]


def test_dependency_output_port_and_upstream_identity_are_signature_inputs(toy_graph):
    scene, registry, _ = toy_graph
    before = execute(scene, registry=registry)
    scene.dependencies[0].output_port = "raw"
    changed_port = execute(scene, previous=before, registry=registry)
    assert changed_port.rebuilt_ids == ["result_beta"]
    scene.nodes.append(Node("new_source", "raw", "unit.source", {"value": 17}))
    scene.dependencies[0].source_id = "new_source"
    after = execute(scene, previous=changed_port, registry=registry)
    assert after.passed
    assert set(after.rebuilt_ids) == {"new_source", "result_beta"}
    assert after.artifacts["result_beta"]["value"] == 34


def test_removed_required_dependency_cannot_reuse_cached_child(toy_graph):
    scene, registry, calls = toy_graph
    before = execute(scene, registry=registry)
    scene.dependencies.clear()
    calls.clear()
    failed = execute(scene, previous=before, registry=registry)
    assert not failed.passed and failed.status == "failed"
    assert calls == [] and "result_beta" not in failed.artifacts
    assert any("required input" in item["reason"] for item in failed.failures)


def test_missing_node_cache_rebuilds_only_that_node_and_downstream(toy_graph):
    scene, registry, _ = toy_graph
    before = execute(scene, registry=registry)
    cache = deepcopy(before.artifacts)
    del cache["result_beta"]
    after = execute(scene, previous=cache, registry=registry)
    assert after.passed and after.rebuilt_ids == ["result_beta"]
    assert set(after.preserved_ids) == {"input_alpha", "independent_sky"}


@pytest.mark.parametrize("metadata", [None, [], {"signature": "corrupt"}])
def test_malformed_cache_metadata_is_a_cache_miss_not_execution_failure(toy_graph, metadata):
    scene, registry, _ = toy_graph
    before = execute(scene, registry=registry)
    cache = deepcopy(before.artifacts)
    cache["input_alpha"]["_execution"] = metadata
    result = execute(scene, previous=cache, registry=registry)
    assert result.passed
    assert set(result.rebuilt_ids) == {"input_alpha", "result_beta"}


def test_missing_external_resource_rebuilds_without_file_hashing(toy_graph, tmp_path):
    scene, registry, calls = toy_graph
    artifact_file = tmp_path / "unit-resource.txt"
    original = registry.get("unit.source")

    def resource_source(node, inputs, context):
        value = original.run(node, inputs, context)
        artifact_file.write_text("unit resource, not model output")
        return {**value, "uri": str(artifact_file)}

    registry.register(replace(original, run=resource_source,
                               cache_validator=lambda artifact, context: artifact_file.is_file()), replace=True)
    before = execute(scene, registry=registry)
    artifact_file.unlink()
    calls.clear()
    after = execute(scene, previous=before, registry=registry)
    assert after.passed and artifact_file.is_file()
    assert set(after.rebuilt_ids) == {"input_alpha", "result_beta"}
    assert after.preserved_ids == ["independent_sky"]
    assert "cache_resource_missing" in {item["reason"] for item in after.node_reports}


def test_unsigned_cache_is_rebuilt_safely(toy_graph):
    scene, registry, _ = toy_graph
    before = execute(scene, registry=registry)
    cache = deepcopy(before.artifacts)
    for artifact in cache.values():
        artifact.pop("_execution")
    after = execute(scene, previous=cache, registry=registry)
    assert after.passed and after.preserved_ids == []


def test_confidence_does_not_promote_a_hypothesis(toy_graph):
    scene, registry, calls = toy_graph
    scene.dependencies[0].status = "proposed"
    scene.dependencies[0].confidence = 1.0
    result = execute(scene, registry=registry)
    assert not result.passed and calls == []
    assert result.dependency_reports[0]["status"] == "proposed"


def test_explicit_lifecycle_validation_then_runtime_geometry_gate(toy_graph):
    scene, registry, calls = toy_graph
    scene.dependencies[0].status = "proposed"
    scene.dependencies[0].confidence = 0.01
    validation = Planner(scene, registry).validate_dependencies()
    assert validation.passed and calls == []
    assert scene.dependencies[0].status == "proposed"
    assert validation.scene.dependencies[0].status == "validated"
    assert validation.dependency_reports[0]["geometry_status"] == "pending"
    plan = Planner(validation.scene, registry).plan()
    assert plan.dependency_reports[0]["status"] == "contract_validated"
    result = execute(validation.scene, registry=registry)
    assert result.passed
    assert result.scene.dependencies[0].status == "executable"
    assert result.dependency_reports[0]["geometry_status"] == "not_required"
    assert result.dependency_reports[0]["check_ids"] == []


def test_unknown_semantic_relation_remains_hypothesis_even_if_legacy_validated(toy_graph):
    scene, registry, calls = toy_graph
    scene.dependencies[0].relation = "looks_near"
    result = execute(scene, registry=registry)
    assert not result.passed and calls == []
    assert result.scene.dependencies[0].status == "proposed"
    assert "hypothesis" in result.dependency_reports[0]["reason"]


@pytest.mark.parametrize("port_field", ["input_port", "output_port"])
def test_bad_port_is_rejected_before_any_registered_callable(toy_graph, port_field):
    scene, registry, calls = toy_graph
    setattr(scene.dependencies[0], port_field, "unknown_port")
    result = execute(scene, registry=registry)
    assert not result.passed and calls == []
    assert result.dependency_reports[0]["status"] == "contract_rejected"


def test_legacy_executable_status_is_revalidated_not_trusted(toy_graph):
    scene, registry, calls = toy_graph
    scene.dependencies[0].status = "executable"
    scene.get_node("input_alpha").kind = "road"
    failed = execute(scene, registry=registry)
    assert not failed.passed and calls == []
    assert failed.scene.dependencies[0].status != "executable"


@pytest.mark.parametrize("phase", ["precondition", "execution", "postcondition"])
def test_failure_phase_blocks_dependents_and_is_reported(toy_graph, phase):
    scene, registry, calls = toy_graph
    original = registry.get("unit.source")

    def fail(*args):
        raise ValueError("intentional unit failure")

    change = ({"preconditions": (fail,)} if phase == "precondition" else
              {"postconditions": (fail,)} if phase == "postcondition" else {"run": fail})
    registry.register(replace(original, **change), replace=True)
    result = execute(scene, registry=registry)
    assert not result.passed
    assert result.failures[0]["phase"] == phase
    assert "result_beta" not in calls and "result_beta" not in result.artifacts
    assert any(item["node_id"] == "result_beta" and item["status"] == "blocked"
               for item in result.node_reports)
    with pytest.raises(ValueError, match="intentional unit failure"):
        execute(scene, registry=registry, raise_on_error=True)


def test_wrong_actual_output_kind_is_a_postcondition_failure(toy_graph):
    scene, registry, _ = toy_graph
    registry.register(replace(registry.get("unit.source"),
                               run=lambda *args: {"kind": "wrong"}), replace=True)
    result = execute(scene, registry=registry)
    assert not result.passed
    assert result.failures[0]["phase"] == "postcondition"
    assert result.artifacts.get("input_alpha") is None


def test_hard_constraint_failure_is_not_reusable_cached_success(demo_scene):
    demo_scene.get_node("road_main").params["height_offset_m"] = 1
    before = execute(demo_scene)
    assert not before.passed
    assert before.artifacts["road_main"]["_execution"]["validation_status"] == "failed"
    assert before.scene.dependencies[0].status == "validated"
    after = execute(demo_scene, previous=before)
    assert not after.passed and "road_main" in after.rebuilt_ids
    assert "background" in after.preserved_ids
    demo_scene.get_node("road_main").params["height_offset_m"] = 0
    repaired = execute(demo_scene, previous=after)
    assert repaired.passed
    assert all(edge.status == "executable" for edge in repaired.scene.dependencies)
    assert any("road_on_ground" in report["check_ids"] for report in repaired.dependency_reports)


def test_late_ready_hard_failure_invalidates_previously_generated_descendants():
    registry = proxy_registry()
    registry.register(OperatorSpec("unit.building", lambda *args: {
        "kind": "building", "position_xyz": [0, 0, 0]}, {}, ("building",)))
    registry.register(OperatorSpec("unit.middle", lambda *args: {"kind": "middle"},
                                   {"source": InputSlot(("building",), relations=("unit_chain",))}, ("middle",)))
    registry.register(OperatorSpec("unit.third", lambda *args: {"kind": "third"},
                                   {"source": InputSlot(("middle",), relations=("unit_chain",))}, ("third",)))
    registry.register(OperatorSpec("unit.terrain", lambda node, *args: {
        "kind": "terrain", "params": {"base_height_m": node.params["height"]}},
        {"source": InputSlot(("third",), relations=("unit_chain",))}, ("terrain",)))
    scene = Scene("late_constraint", [Node("a", "building", "unit.building"),
                                      Node("b", "middle", "unit.middle"),
                                      Node("c", "third", "unit.third"),
                                      Node("d", "terrain", "unit.terrain", {"height": 1})],
                  [Dependency("a", "b", "unit_chain"), Dependency("b", "c", "unit_chain"),
                   Dependency("c", "d", "unit_chain")],
                  [Constraint("late_ground", "ground_contact", ["a", "d"])])
    first = execute(scene, registry=registry)
    assert not first.passed
    assert set(first.artifacts) == {"a", "b", "c", "d"}
    assert all(artifact["_execution"]["validation_status"] == "failed" for artifact in first.artifacts.values())
    assert all(edge.status == "validated" for edge in first.scene.dependencies)
    middle_edge = next(report for report in first.dependency_reports if report["source_id"] == "b")
    assert middle_edge["geometry_status"] == "failed_or_blocked"
    assert middle_edge["check_ids"] == ["late_ground"]
    assert middle_edge["constraint_evidence"][0]["value"] == 1
    assert middle_edge["blocked_by"] == ["b", "c"]
    retried = execute(scene, previous=first, registry=registry)
    assert not retried.passed and retried.preserved_ids == []
    assert set(retried.rebuilt_ids) == {"a", "b", "c", "d"}
    scene.get_node("d").params["height"] = 0
    repaired = execute(scene, previous=retried, registry=registry)
    assert repaired.passed
    assert all(edge.status == "executable" for edge in repaired.scene.dependencies)


def test_cycle_produces_planning_failure_without_callable_execution(toy_graph):
    _, registry, calls = toy_graph
    registry.register(OperatorSpec("unit.loop", lambda *args: calls.append("called"),
                                   {"upstream": InputSlot(("raw",), relations=("unit_loop",))}, ("raw",)))
    scene = Scene("loop", [Node("left", "raw", "unit.loop"), Node("right", "raw", "unit.loop")],
                  [Dependency("left", "right", "unit_loop"), Dependency("right", "left", "unit_loop")])
    result = execute(scene, registry=registry)
    assert not result.passed and calls == []
    assert result.failures[0]["phase"] == "planning"
    assert "Cycle" in result.failures[0]["reason"]


def test_unknown_semantic_backlink_does_not_form_an_execution_cycle(toy_graph):
    scene, registry, _ = toy_graph
    scene.dependencies.append(Dependency("result_beta", "input_alpha", "semantically_near"))
    result = execute(scene, registry=registry)
    assert result.passed
    assert result.scene.dependencies[1].status == "proposed"


def test_unknown_semantic_relation_does_not_propagate_explicit_edits(toy_graph):
    scene, registry, _ = toy_graph
    scene.dependencies.append(Dependency("input_alpha", "independent_sky", "looks_near"))
    before = execute(scene, registry=registry)
    result = execute(scene, previous=before, registry=registry, changed_ids={"input_alpha"})
    assert result.passed
    assert result.preserved_ids == ["independent_sky"]


def test_structured_pre_and_postcondition_evidence_is_preserved(toy_graph):
    scene, registry, _ = toy_graph
    original = registry.get("unit.source")
    registry.register(replace(original,
                               preconditions=(lambda *args: {"passed": True, "reason": "unit input admissible"},),
                               postconditions=(lambda *args: {"passed": True, "value": 3},)), replace=True)
    result = execute(scene, registry=registry)
    report = next(item for item in result.node_reports if item["node_id"] == "input_alpha")
    assert result.passed
    assert report["preconditions"]["evidence"][0]["reason"] == "unit input admissible"
    assert report["postconditions"]["evidence"][0]["value"] == 3


def test_structured_failed_condition_cannot_be_treated_as_truthy_success(toy_graph):
    scene, registry, _ = toy_graph
    registry.register(replace(registry.get("unit.source"),
                               postconditions=(lambda *args: {"passed": False, "reason": "unit violation"},)),
                      replace=True)
    result = execute(scene, registry=registry)
    assert not result.passed
    assert result.failures[0]["phase"] == "postcondition"
    assert "unit violation" in result.failures[0]["reason"]
    assert result.failures[0]["check_evidence"]["passed"] is False


def test_explicit_dirty_hint_is_optional_but_still_forces_known_closure(toy_graph):
    scene, registry, _ = toy_graph
    before = execute(scene, registry=registry)
    after = execute(scene, previous=before, changed_ids={"input_alpha"}, registry=registry)
    assert set(after.rebuilt_ids) == {"input_alpha", "result_beta"}
    assert after.preserved_ids == ["independent_sky"]


def test_ir_ports_and_executable_status_round_trip(demo_scene):
    demo_scene.dependencies[0].input_port = "terrain"
    demo_scene.dependencies[0].output_port = "terrain"
    demo_scene.dependencies[0].status = "executable"
    assert Scene.from_dict(demo_scene.to_dict()).to_dict() == demo_scene.to_dict()


def test_registered_contract_is_inspectable_and_duplicate_registration_rejected(toy_graph):
    _, registry, _ = toy_graph
    spec = registry.get("unit.target")
    description = spec.to_dict()
    assert description["input_slots"]["source"]["kinds"] == ["raw"]
    assert description["seed_source"] == "Node.seed"
    with pytest.raises(ValueError, match="already registered"):
        registry.register(spec)

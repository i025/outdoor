"""Independent admission edge cases; these are contract tests, not backend evidence."""
from dataclasses import replace

from outdoorgraph.constraints import evaluate_constraints
from outdoorgraph.execution import InputSlot, OperatorSpec, Planner, execute, proxy_registry
from outdoorgraph.relations import RelationSpec
from outdoorgraph.ir import Constraint, Dependency, Node, Scene


def test_contact_evidence_for_another_terrain_cannot_certify_this_relation():
    registry = proxy_registry()
    # Emulate an erroneous trusted implementation to test the relation gate:
    # the declared support is at 3m, while the object was incorrectly placed at 0m.
    registry.register(replace(registry.get("building.proxy"), run=lambda *args: {
        "kind": "building", "position_xyz": [0, 0, 0], "size_m": [1, 1, 1]}), replace=True)
    scene = Scene("wrong_support_proof", [
        Node("declared_support", "terrain", "terrain.heightfield", {"base_height_m": 3}),
        Node("other_support", "terrain", "terrain.heightfield", {"base_height_m": 0}),
        Node("subject", "building", "building.proxy", {"position_xy": [0, 0]}),
    ], [Dependency("declared_support", "subject", "supported_by_terrain", status="validated")],
        [Constraint("unrelated_contact", "ground_contact", ["subject", "other_support"],
                    {"tolerance_m": 0.01})])
    control = evaluate_constraints(scene, {
        "subject": {"position_xyz": [0, 0, 0]},
        "other_support": {"params": {"base_height_m": 0}},
    })[0]
    assert control["passed"], "The control evidence must pass against the other terrain"
    result = execute(scene, registry=registry)
    assert not result.passed, "Passing evidence for a different support cannot admit this dependency"
    report = result.dependency_reports[0]
    assert report["status"] != "executable"
    assert result.scene.dependencies[0].status != "executable"


def test_explicit_validation_does_not_revive_rejected_optional_relation():
    registry = proxy_registry()
    scene = Scene("rejected_stays_rejected", [
        Node("ground", "terrain", "terrain.heightfield"),
        Node("path", "road", "road.polyline", {"control_points_xy": [[0, 0], [1, 0]]}),
        Node("region", "vegetation", "vegetation.scatter", {"count": 1, "min_spacing_m": 0}),
    ], [Dependency("ground", "path", "samples_terrain_height", status="validated"),
        Dependency("ground", "region", "samples_terrain_height", status="validated"),
        Dependency("path", "region", "road_exclusion_buffer", status="rejected")])
    validation = Planner(scene, registry).validate_dependencies(only_proposed=False)
    assert validation.scene.dependencies[2].status == "rejected"
    result = execute(validation.scene, registry=registry)
    assert result.passed
    assert result.scene.dependencies[2].status == "rejected"
    assert "path" not in result.artifacts["region"].get("input_bindings", {}).get("roads", [])


def test_declared_symlink_retargeting_invalidates_the_consumer_only(tmp_path):
    first_path, second_path = tmp_path / "first.bin", tmp_path / "second.bin"
    first_path.write_bytes(b"aaaa")
    second_path.write_bytes(b"bbbb")
    declared_path = tmp_path / "declared.bin"
    declared_path.symlink_to(first_path)
    registry = proxy_registry()

    def read(node, inputs, context):
        from pathlib import Path
        return {"kind": "raw", "value": Path(context["external_input_paths"]["asset"]).read_text()}

    registry.register(OperatorSpec("review.file", read, {}, ("raw",),
                                   external_inputs={"asset": declared_path}))
    scene = Scene("symlink_retarget", [Node("consumer", "raw", "review.file"),
                                       Node("unrelated", "background", "background.constant")])
    before = execute(scene, registry=registry)
    assert before.passed and before.artifacts["consumer"]["value"] == "aaaa"
    declared_path.unlink()
    declared_path.symlink_to(second_path)
    after = execute(scene, previous=before, registry=registry)
    assert after.passed and after.artifacts["consumer"]["value"] == "bbbb"
    assert after.rebuilt_ids == ["consumer"] and after.preserved_ids == ["unrelated"]
    assert before.artifacts["unrelated"] == after.artifacts["unrelated"]
    assert (before.artifacts["consumer"]["_execution"]["signature"]
            != after.artifacts["consumer"]["_execution"]["signature"])


def test_failed_physical_relation_blocks_actual_downstream_callable():
    registry = proxy_registry()
    called = []
    registry.register(replace(registry.get("road.polyline"), run=lambda *args: {
        "kind": "road", "width_m": 1, "points": [[0, 0, 5], [1, 0, 5]]}), replace=True)
    registry.register(OperatorSpec("review.consume", lambda *args: called.append("consumer") or {
        "kind": "processed"}, {"source": InputSlot(("road",))}, ("processed",)))
    registry.register_relation(RelationSpec("review_consumes", ("road",), ("processed",),
                                            validators=(lambda *args: True,)))
    scene = Scene("failed_relation_before_consumption", [
        Node("ground", "terrain", "terrain.heightfield"),
        Node("bad_road", "road", "road.polyline", {"control_points_xy": [[0, 0], [1, 0]]}),
        Node("consumer", "processed", "review.consume"),
    ], [Dependency("ground", "bad_road", "samples_terrain_height", status="validated"),
        Dependency("bad_road", "consumer", "review_consumes", status="validated")])
    result = execute(scene, registry=registry)
    assert not result.passed
    assert called == [], "A failed physical dependency must gate downstream invocation, not only final status"
    assert "consumer" not in result.artifacts
    assert any(r["node_id"] == "consumer" and r["status"] == "blocked" for r in result.node_reports)

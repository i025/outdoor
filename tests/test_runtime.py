import copy

import pytest

from outdoorgraph.constraints import evaluate_constraints
from outdoorgraph.ir import Dependency
from outdoorgraph.operators import height_at
from outdoorgraph.runtime import downstream, execute


def test_initial_scene_passes_constraints(demo_scene):
    state = execute(demo_scene)
    assert state.passed, state.constraint_reports
    assert len(state.artifacts["forest_main"]["positions"]) >= 25
    assert state.preserved_ids == []


def test_repeatable_with_fixed_seed(demo_scene):
    assert execute(demo_scene).artifacts == execute(demo_scene).artifacts


def test_terrain_edit_propagates_but_preserves_background(demo_scene):
    before = execute(demo_scene)
    updated = copy.deepcopy(demo_scene)
    updated.get_node("terrain_main").params["local_bump"]["amplitude_m"] = 2.5
    after = execute(updated, before.artifacts, {"terrain_main"})
    assert after.passed, after.constraint_reports
    assert set(after.rebuilt_ids) == {"terrain_main", "road_main", "forest_main", "building_main"}
    assert after.preserved_ids == ["background"]
    assert before.artifacts["background"] == after.artifacts["background"]
    assert before.artifacts["road_main"] != after.artifacts["road_main"]
    # The building's support is re-sampled, not kept at a stale ground height.
    building = after.artifacts["building_main"]
    x, y, z = building["position_xyz"]
    assert z == pytest.approx(height_at(after.artifacts["terrain_main"], x, y))


def test_hypothetical_edge_does_not_trigger_recompute(demo_scene):
    scene = copy.deepcopy(demo_scene)
    scene.dependencies.append(Dependency("background", "forest_main", "unverified_relation", .2, "proposed"))
    assert downstream(scene, {"background"}) == {"background"}


def test_invalid_previous_node_set_rejected(demo_scene):
    with pytest.raises(ValueError, match="do not match"):
        execute(demo_scene, previous={"terrain_main": {}}, changed_ids={"terrain_main"})


def test_constraint_violation_not_silently_fixed(demo_scene):
    scene = execute(demo_scene)
    bad_artifacts = copy.deepcopy(scene.artifacts)
    bad_artifacts["building_main"]["position_xyz"][2] += 2.0
    failures = evaluate_constraints(demo_scene, bad_artifacts)
    report = next(item for item in failures if item["id"] == "house_on_ground")
    assert not report["passed"]
    assert report["value"] > report["limit"]


def test_unknown_generator_fails_loudly(demo_scene):
    demo_scene.get_node("forest_main").operator = "agent.untrusted_python"
    with pytest.raises(ValueError, match="Unknown procedural operator"):
        execute(demo_scene)


def test_in_place_terrain_edit_preserves_previous_artifacts(demo_scene):
    before = execute(demo_scene)
    snapshot = copy.deepcopy(before.artifacts)
    demo_scene.get_node("terrain_main").params["local_bump"]["amplitude_m"] = 2.5
    assert before.artifacts == snapshot
    after = execute(demo_scene, before.artifacts, {"terrain_main"})
    assert after.passed, after.constraint_reports
    assert after.artifacts["terrain_main"] != before.artifacts["terrain_main"]
    assert before.artifacts == snapshot


@pytest.mark.parametrize("width", [0, -1, float("nan"), float("inf"), -float("inf")],
                         ids=["zero", "negative", "nan", "infinity", "negative_infinity"])
def test_reject_nonpositive_or_nonfinite_road_width(demo_scene, width):
    demo_scene.get_node("road_main").params["width_m"] = width
    with pytest.raises(ValueError, match="width_m must be finite and positive"):
        execute(demo_scene)

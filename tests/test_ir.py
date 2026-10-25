import copy

import pytest

from outdoorgraph.ir import Dependency, Scene
from outdoorgraph.validation import SceneValidationError, topological_ids, validate_scene


def test_ir_round_trip(demo_scene):
    assert Scene.from_dict(demo_scene.to_dict()).to_dict() == demo_scene.to_dict()


def test_reject_duplicate_node_id(demo_scene):
    scene = copy.deepcopy(demo_scene)
    scene.nodes.append(copy.deepcopy(scene.nodes[0]))
    with pytest.raises(SceneValidationError, match="unique"):
        validate_scene(scene)


def test_reject_missing_dependency_target(demo_scene):
    scene = copy.deepcopy(demo_scene)
    scene.dependencies.append(Dependency("terrain_main", "missing", "unsupported"))
    with pytest.raises(SceneValidationError, match="missing"):
        validate_scene(scene)


def test_reject_validated_cycle(demo_scene):
    scene = copy.deepcopy(demo_scene)
    scene.dependencies.append(Dependency("forest_main", "terrain_main", "bad_feedback"))
    with pytest.raises(SceneValidationError, match="Cycle"):
        validate_scene(scene)


def test_proposed_edges_not_executable(demo_scene):
    scene = copy.deepcopy(demo_scene)
    scene.dependencies.append(Dependency("forest_main", "terrain_main", "hypothetical", 0.3, "proposed"))
    validate_scene(scene)
    order = topological_ids(scene)
    assert order.index("terrain_main") < order.index("forest_main")


def test_bad_confidence_fails(demo_scene):
    scene = copy.deepcopy(demo_scene)
    scene.dependencies[0].confidence = 1.5
    with pytest.raises(SceneValidationError, match="confidence"):
        validate_scene(scene)


@pytest.mark.parametrize("rule", [
    "ground_contact", "road_contact", "road_grade",
    "vegetation_road_clearance", "vegetation_min_count",
])
@pytest.mark.parametrize("extra_node", [False, True], ids=["missing", "extra"])
def test_reject_invalid_constraint_node_count(demo_scene, rule, extra_node):
    constraint = next(item for item in demo_scene.constraints if item.rule == rule)
    if extra_node:
        constraint.node_ids.append("background")
    else:
        constraint.node_ids.pop()
    with pytest.raises(SceneValidationError, match="constraint node count"):
        validate_scene(demo_scene)

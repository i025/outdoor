"""Geometry contracts shared by the proxy planner and the real Blender worker."""

from copy import deepcopy
import math

import pytest

from outdoorgraph.constraints import evaluate_constraints
from outdoorgraph.ir import Constraint, Node, Scene
from outdoorgraph.operators import (
    building_proxy,
    height_at,
    road_polyline,
    terrain_heightfield,
    vegetation_scatter,
)


def terrain(**params):
    return terrain_heightfield(Node("ground", "terrain", "terrain.heightfield", params,
                                    bounds_xy=[-10, -10, 10, 10]), {})


def check(rule, artifacts, **params):
    node_ids = {"road_contact": ["path", "ground"], "road_grade": ["path"],
                "ground_contact": ["house", "ground"], "terrain_continuity": ["west", "east"],
                "road_topology": ["path"]}.get(rule, [])
    scene = Scene("contact", [], constraints=[Constraint("test", rule, node_ids, params)])
    return evaluate_constraints(scene, artifacts)[0]


def test_legacy_road_contact_checks_between_control_points():
    ground = terrain(local_bump={"x": 5, "y": 0, "sigma_m": 0.5, "amplitude_m": 2})
    path = {"kind": "road", "width_m": 1, "points": [[0, 0, 0], [10, 0, 0]]}
    report = check("road_contact", {"ground": ground, "path": path}, tolerance_m=0.01)
    assert not report["passed"]
    assert report["value"] == pytest.approx(2)
    assert report["worst_point_xyz"] == pytest.approx([5, 0, 0])
    assert report["sample_count"] > 2


def test_road_samples_surface_without_replacing_legacy_control_points():
    ground = terrain(local_bump={"x": 0, "y": 0, "sigma_m": 1, "amplitude_m": 1})
    node = Node("path", "road", "road.polyline",
                {"control_points_xy": [[-2, 0], [2, 0]], "sample_spacing_m": 0.25})
    path = road_polyline(node, {"ground": ground})
    assert path["points"] == [[-2, 0, math.exp(-2)], [2, 0, math.exp(-2)]]
    assert [0, 0, 1] in path["sampled_points"]
    assert len(path["sampled_points"]) > len(path["points"])
    report = check("road_contact", {"ground": ground, "path": path}, tolerance_m=0.01)
    assert report["passed"], report
    assert report["sample_count"] > len(path["sampled_points"])


def test_road_grade_uses_rendered_dense_polyline():
    ground = terrain(local_bump={"x": 0, "y": 0, "sigma_m": 1, "amplitude_m": 1})
    path = road_polyline(Node("path", "road", "road.polyline",
                              {"control_points_xy": [[-2, 0], [2, 0]],
                               "sample_spacing_m": 0.25}), {"ground": ground})
    # Both authored endpoints have the same height, but the actual road climbs a hill.
    assert path["points"][0][2] == path["points"][-1][2]
    report = check("road_grade", {"path": path}, max_grade=0.1)
    assert not report["passed"]
    assert report["value"] > 0.5


def test_contact_checks_actual_samples_instead_of_reprojecting_them():
    ground = terrain()
    path = {"kind": "road", "width_m": 1, "points": [[0, 0, 0], [2, 0, 0]],
            "sampled_points": [[0, 0, 0], [1, 0, 1], [2, 0, 0]]}
    report = check("road_contact", {"ground": ground, "path": path}, tolerance_m=0.01)
    assert not report["passed"]
    assert report["value"] == 1


def test_contact_subdivides_even_short_rendered_segments():
    ground = terrain(local_bump={"sigma_m": 0.02, "amplitude_m": 1})
    path = {"kind": "road", "points": [[-0.1, 0, math.exp(-12.5)],
                                           [0.1, 0, math.exp(-12.5)]]}
    report = check("road_contact", {"ground": ground, "path": path},
                   sample_spacing_m=1, tolerance_m=0.05)
    assert not report["passed"]
    assert report["value"] > 0.99
    assert report["sample_count"] >= 3


def test_compact_edit_has_exactly_local_support_and_a_smooth_boundary():
    ground = terrain(base_height_m=3, local_bump={"profile": "compact", "radius_m": 2,
                                                 "amplitude_m": 4})
    assert height_at(ground, 0, 0) == 7
    for x, y in [(2, 0), (0, -2), (3, 4), (1e6, 1e6)]:
        assert height_at(ground, x, y) == 3
    assert height_at(ground, 2 - 1e-4, 0) - 3 < 1e-10


def test_compact_edit_preserves_shared_patch_seam_and_unaffected_region():
    west = terrain(base_height_m=0.1, slope_x=0.02, slope_y=0.01,
                   local_bump={"profile": "compact", "x": 6, "y": 8,
                               "radius_m": 4, "amplitude_m": 1.2})
    east = terrain(base_height_m=0.1, slope_x=0.02, slope_y=0.01)
    baseline = deepcopy(west)
    baseline["params"]["local_bump"]["amplitude_m"] = 0
    assert height_at(west, 6, 8) > height_at(baseline, 6, 8)
    for y in [0, 1, 3.2, 8, 11, 16]:
        assert height_at(west, 12, y) == height_at(east, 12, y)
        assert height_at(west, 18, y) == height_at(baseline, 18, y)


def test_existing_gaussian_default_is_preserved():
    ground = terrain(local_bump={"sigma_m": 2, "amplitude_m": 3})
    assert height_at(ground, 2, 0) == pytest.approx(3 * math.exp(-0.5))
    assert height_at(ground, 4, 0) > 0


def test_scatter_fixed_seed_and_terrain_edit_preserve_xy_samples():
    ground = terrain()
    node = Node("grass", "vegetation", "vegetation.scatter",
                {"count": 12, "min_spacing_m": 0.25}, [-2, -2, 2, 2], seed=123)
    original = vegetation_scatter(node, {"ground": ground})
    assert original == vegetation_scatter(node, {"ground": ground})
    edited = deepcopy(ground)
    edited["params"]["local_bump"] = {"profile": "compact", "radius_m": 4,
                                        "amplitude_m": 1}
    updated = vegetation_scatter(node, {"ground": edited})
    assert len(original["positions"]) == 12
    assert [p[:2] for p in original["positions"]] == [p[:2] for p in updated["positions"]]
    assert all(p[2] == 0 for p in original["positions"])
    assert all(p[2] > 0 for p in updated["positions"])


@pytest.mark.parametrize("field,value", [("base_height_m", math.nan), ("slope_x", math.inf),
                                         ("noise_amplitude_m", -math.inf),
                                         ("noise_scale_m", 0)])
def test_invalid_terrain_parameters_fail_explicitly(field, value):
    with pytest.raises(ValueError, match="finite|positive"):
        terrain(**{field: value})


@pytest.mark.parametrize("bump", [{"profile": "compact", "radius_m": 0},
                                  {"profile": "compact", "radius_m": math.inf},
                                  {"sigma_m": -1}, {"amplitude_m": math.nan},
                                  {"profile": "unknown"}])
def test_invalid_bump_parameters_fail_explicitly(bump):
    with pytest.raises(ValueError, match="finite|positive|Unknown"):
        terrain(local_bump=bump)


@pytest.mark.parametrize("params", [{"sample_spacing_m": 0}, {"sample_spacing_m": math.inf},
                                    {"height_offset_m": math.nan},
                                    {"control_points_xy": [[0, 0], [math.nan, 1]]},
                                    {"control_points_xy": [[0, 0], [0, 0]]}])
def test_invalid_road_parameters_fail_explicitly(params):
    config = {"control_points_xy": [[0, 0], [2, 0]], **params}
    with pytest.raises(ValueError, match="finite|positive|distinct"):
        road_polyline(Node("path", "road", "road.polyline", config), {"ground": terrain()})


@pytest.mark.parametrize("value", [math.nan, math.inf, -0.1])
def test_invalid_constraint_tolerance_cannot_pass(value):
    artifacts = {"ground": terrain(), "house": {"position_xyz": [0, 0, 0]}}
    with pytest.raises(ValueError, match="tolerance_m"):
        check("ground_contact", artifacts, tolerance_m=value)


def test_nonfinite_artifact_geometry_cannot_pass():
    path = {"kind": "road", "points": [[0, 0, 0], [2, 0, math.nan]]}
    with pytest.raises(ValueError, match="finite"):
        check("road_contact", {"ground": terrain(), "path": path})


def test_invalid_building_size_and_scatter_buffer_fail_explicitly():
    with pytest.raises(ValueError, match="finite"):
        building_proxy(Node("house", "building", "building.proxy",
                            {"position_xy": [0, 0], "size_m": [1, 1, math.inf]}),
                       {"ground": terrain()})
    with pytest.raises(ValueError, match="finite"):
        vegetation_scatter(Node("grass", "vegetation", "vegetation.scatter",
                                {"road_buffer_m": math.nan}), {"ground": terrain()})


def test_unknown_hard_constraint_is_never_accepted():
    with pytest.raises(ValueError, match="Unknown constraint"):
        check("unimplemented_geometry", {})


@pytest.mark.parametrize("first_bounds,second_bounds", [
    ([0, 0, 12, 16], [12, 0, 24, 16]),
    ([12, 0, 24, 16], [0, 0, 12, 16]),
    ([0, 0, 12, 16], [0, 16, 12, 32]),
    ([0, 16, 12, 32], [0, 0, 12, 16]),
])
def test_terrain_continuity_checks_each_orientation(first_bounds, second_bounds):
    first, second = terrain(slope_x=0.02, slope_y=0.01), terrain(slope_x=0.02, slope_y=0.01)
    first["bounds_xy"], second["bounds_xy"] = first_bounds, second_bounds
    report = check("terrain_continuity", {"west": first, "east": second}, tolerance_m=1e-9)
    assert report["passed"]
    assert report["value"] == 0
    assert report["sample_count"] > 2


def test_terrain_continuity_detects_an_interior_seam_bump():
    first = terrain()
    second = terrain(local_bump={"profile": "compact", "x": 12, "y": 8,
                                 "radius_m": 2, "amplitude_m": 1})
    first["bounds_xy"], second["bounds_xy"] = [0, 0, 12, 16], [12, 0, 24, 16]
    # The seam endpoints meet, but its middle has a one-meter step.
    assert height_at(second, 12, 0) == height_at(first, 12, 0)
    assert height_at(second, 12, 16) == height_at(first, 12, 16)
    report = check("terrain_continuity", {"west": first, "east": second}, tolerance_m=0.01)
    assert not report["passed"]
    assert report["value"] == 1
    assert report["worst_point_xy"] == [12, 8]


@pytest.mark.parametrize("second_bounds", [[13, 0, 24, 16], [11, 0, 24, 16],
                                           [12, 16, 24, 32], [0, 0, 12, 16]])
def test_terrain_continuity_rejects_missing_shared_edge(second_bounds):
    first, second = terrain(), terrain()
    first["bounds_xy"], second["bounds_xy"] = [0, 0, 12, 16], second_bounds
    report = check("terrain_continuity", {"west": first, "east": second})
    assert not report["passed"]
    assert report["sample_count"] == 0
    assert "adjacent edge" in report["reason"]


def test_road_topology_passes_dense_road_with_required_endpoints():
    path = road_polyline(Node("path", "road", "road.polyline",
                              {"control_points_xy": [[0, 0], [0, 4], [2, 4]],
                               "sample_spacing_m": 0.25}), {"ground": terrain()})
    report = check("road_topology", {"path": path}, required_endpoints_xy=[[0, 0], [2, 4]],
                   tolerance_m=0.01)
    assert report["passed"]
    assert report["minimum_segment_length_m"] > 0
    assert report["maximum_segment_length_m"] <= 0.25
    assert report["segment_count"] == len(path["sampled_points"]) - 1


def test_road_topology_checks_actual_geometry_endpoint_instead_of_control_points():
    path = {"points": [[0, 0, 0], [2, 0, 0]],
            "sampled_points": [[0, 0, 0], [2, 1, 0]]}
    report = check("road_topology", {"path": path}, required_endpoints_xy=[[0, 0], [2, 0]],
                   tolerance_m=0.01)
    assert not report["passed"]
    assert report["value"] == 1
    assert "endpoints" in report["reason"]


@pytest.mark.parametrize("path,reason", [
    ({"points": []}, "at least two"),
    ({"points": [[0, 0, 0], [0, 0, 1]]}, "zero-length"),
    ({"points": [[0, 0, 0], [1, 0, math.nan]]}, "finite"),
    ({"points": [[0, 0], [1, 0]]}, "XYZ"),
    ({"points": [[0, 0, 0], [1, 0, 0]], "sampling_spacing_m": 0.25}, "segment length"),
])
def test_road_topology_reports_invalid_geometry(path, reason):
    report = check("road_topology", {"path": path})
    assert not report["passed"]
    assert reason in report["reason"]


def test_road_topology_reports_explicit_gap_threshold_and_bad_endpoints():
    path = {"points": [[0, 0, 0], [1, 0, 0], [5, 0, 0]]}
    report = check("road_topology", {"path": path}, max_segment_length_m=2)
    assert not report["passed"]
    assert report["value"] == 4
    assert report["limit"] == 2
    report = check("road_topology", {"path": path}, required_endpoints_xy=[[0, 0]])
    assert not report["passed"]
    assert "start and end" in report["reason"]

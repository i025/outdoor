import math

import pytest

from outdoorgraph.adapters.graph_backend_data import allowed_region_faces, heightfield_mesh, ribbon_xy
from outdoorgraph.operators import distance_to_road, height_at


def test_heightfield_render_mesh_reuses_existing_height_function_and_exact_boundary():
    terrain = {"bounds_xy": [0, 0, 1, 1], "params": {"slope_x": .4, "slope_y": .2}}
    vertices, faces = heightfield_mesh(terrain, .3, height_at)
    assert len(vertices) == 25
    assert len(faces) == 32
    assert vertices[-1] == pytest.approx([1, 1, .6])
    assert all(v[2] == height_at(terrain, v[0], v[1]) for v in vertices)


def test_ribbon_discretizes_actual_dense_centerline_across_full_width():
    vertices, faces = ribbon_xy([[0, 0, 0], [0, 1, 0], [0, 2, 0]], 2, .25)
    assert len(vertices) == 27
    assert len(faces) == 32
    assert min(v[0] for v in vertices) == -1
    assert max(v[0] for v in vertices) == 1
    assert {v[1] for v in vertices} == {0, 1, 2}


def test_conservative_face_mask_excludes_triangle_crossing_road_between_vertices():
    road = {"points": [[0, -10, 0], [0, 10, 0]], "width_m": .1}
    vertices = [[-2, -2, 0], [2, -2, 0], [0, 2, 0], [5, 5, 0], [6, 5, 0], [5, 6, 0]]
    result = allowed_region_faces(vertices, [[0, 1, 2], [3, 4, 5]], [-10, -10, 10, 10],
                                  [road], .1, distance_to_road)
    assert result == [False, True]


def test_manual_face_mask_and_region_bounds_are_both_required():
    vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    assert allowed_region_faces(vertices, [[0, 1, 2]], [0, 0, 2, 2], [], 0,
                                distance_to_road, [False]) == [False]
    assert allowed_region_faces(vertices, [[0, 1, 2]], [0, 0, .5, .5], [], 0,
                                distance_to_road, [True]) == [False]


@pytest.mark.parametrize("spacing", [0, -1, math.inf, math.nan])
def test_invalid_mesh_spacing_fails_before_blender_mutation(spacing):
    with pytest.raises(ValueError):
        heightfield_mesh({"bounds_xy": [0, 0, 1, 1], "params": {}}, spacing, height_at)


def test_worker_mesh_budget_prevents_accidental_unbounded_scene():
    with pytest.raises(ValueError, match="budget"):
        heightfield_mesh({"bounds_xy": [0, 0, 1000, 1000], "params": {}}, .01, height_at)

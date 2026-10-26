"""Opt-in regressions that execute the actual existing Blender/Infinigen stack.

No synthetic backend result or mocked subprocess is used in this module.
"""
import copy
import json
import os
from pathlib import Path
import shutil

import pytest

from outdoorgraph.adapters.graph_pipeline import load_graph_config, run_graph, run_graph_experiment
from outdoorgraph.ir import Constraint, Dependency, Node, Scene

ROOT = Path(__file__).resolve().parents[1]
CONFIG = os.environ.get("OUTDOORGRAPH_BLENDER_CONFIG")
pytestmark = pytest.mark.skipif(not CONFIG, reason="Set OUTDOORGRAPH_BLENDER_CONFIG to run real Blender/Infinigen")


def read(path):
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def experiment(tmp_path_factory):
    directory = os.environ.get("OUTDOORGRAPH_GRAPH_OUTPUT")
    out = Path(directory) if directory else tmp_path_factory.mktemp("real_graph") / "experiment"
    run_graph_experiment(ROOT / "configs/unified_scene.json", Path(CONFIG), out)
    return out


def test_real_graph_parameter_edits_and_automatic_invalidation(experiment):
    initial = read(experiment / "initial/state.json")
    a = read(experiment / "terrain_edit/state.json")
    b = read(experiment / "road_edit/state.json")
    assert initial["passed"] and a["passed"] and b["passed"]
    assert set(a["rebuilt_ids"]) == {"upland_profile", "west_ground", "access_track", "meadow", "wayside_shelter"}
    assert set(b["rebuilt_ids"]) == {"access_track", "meadow"}
    assert initial["artifacts"]["access_track"] != a["artifacts"]["access_track"]
    assert b["artifacts"]["access_track"]["width_m"] == 2.8
    assert {r["backend"] for r in initial["node_reports"]} >= {"proxy", "blender"}


def test_preserved_real_collections_instances_and_metadata(experiment):
    for previous, current in (("initial", "terrain_edit"), ("terrain_edit", "road_edit"), ("road_edit", "unchanged")):
        before = read(experiment / previous / "state.json")
        after = read(experiment / current / "state.json")
        old_nodes = read(experiment / previous / "blender/objects.json")["nodes"]
        new_nodes = read(experiment / current / "blender/objects.json")["nodes"]
        for node_id in after["preserved_ids"]:
            assert before["artifacts"][node_id] == after["artifacts"][node_id]
            if node_id in old_nodes:
                assert old_nodes[node_id] == new_nodes[node_id], node_id
    unchanged = read(experiment / "unchanged/state.json")
    assert unchanged["rebuilt_ids"] == []
    assert len(unchanged["preserved_ids"]) == 9


def test_real_scenes_reopen_render_and_geometric_constraints(experiment):
    for name in ("initial", "terrain_edit", "road_edit", "unchanged", "fresh_repeat"):
        backend = experiment / name / "blender"
        assert read(backend / "validation.json")["passed"]
        assert read(backend / "reopen_verification.json")["passed"]
        assert (backend / "scene.blend").stat().st_size > 1024
        assert (backend / "scene.glb").stat().st_size > 1024
        assert (backend / "render.png").stat().st_size > 1024
        state = read(experiment / name / "state.json")
        road = state["artifacts"]["access_track"]
        assert len(road["sampled_points"]) > len(road["points"])
        contact = next(c for c in state["constraint_reports"] if c["id"] == "track_contact")
        assert contact["passed"] and contact["sample_count"] > len(road["sampled_points"])


def test_native_fixed_seed_determinism_after_incremental_edits(experiment):
    incremental = read(experiment / "road_edit/blender/objects.json")["nodes"]
    fresh = read(experiment / "fresh_repeat/blender/objects.json")["nodes"]
    assert incremental.keys() == fresh.keys()
    for node_id in incremental:
        a, b = incremental[node_id], fresh[node_id]
        assert a["instances"] == b["instances"], node_id
        # Blender may allocate different internal datablock names after edits;
        # persistent IDs, world transforms and geometry must remain deterministic.
        old_objects = {obj["id"]: obj for obj in a["objects"]}
        new_objects = {obj["id"]: obj for obj in b["objects"]}
        assert old_objects.keys() == new_objects.keys(), node_id
        for object_id in old_objects:
            old, new = old_objects[object_id], new_objects[object_id]
            for field in ("matrix_world", "vertices", "faces", "properties"):
                assert old.get(field) == new.get(field), (object_id, field)


def test_missing_blend_cache_rebuilds_real_backend_safely(experiment):
    missing = experiment / "missing_cache_input"
    missing.mkdir()
    shutil.copyfile(experiment / "road_edit/state.json", missing / "state.json")
    scene = Scene.from_dict(read(experiment / "road_edit/scene_ir.json"))
    target = experiment / "cache_recovery"
    run_graph(scene, load_graph_config(Path(CONFIG)), target, previous=missing)
    state = read(target / "state.json")
    assert state["passed"]
    assert set(state["preserved_ids"]) == {"upland_profile", "lowland_profile", "sky_tint"}
    assert set(state["rebuilt_ids"]) == {"west_ground", "east_ground", "access_track", "meadow", "remote_meadow", "wayside_shelter"}
    assert read(target / "blender/reopen_verification.json")["passed"]


def test_actual_hard_constraint_failure_is_saved_and_rejected(experiment):
    previous = experiment / "road_edit"
    modified = copy.deepcopy(Scene.from_dict(read(previous / "scene_ir.json")))
    modified.get_node("access_track").params["height_offset_m"] = 0.4
    original_mtime = (previous / "blender/scene.blend").stat().st_mtime_ns
    out = experiment / "hard_failure"
    with pytest.raises((RuntimeError, ValueError)):
        run_graph(modified, load_graph_config(Path(CONFIG)), out, previous=previous)
    state = read(out / "state.json")
    assert not state["passed"] and state["status"] == "failed"
    contact = next(c for c in state["constraint_reports"] if c["id"] == "track_contact")
    assert not contact["passed"] and contact["status"] == "blocked"
    failure = next(f for f in state["failures"] if f.get("node_id") == "access_track")
    assert failure["phase"] == "postcondition"
    evidence = failure["check_evidence"]
    assert evidence["rule"] == "actual_road_mesh_contact" and not evidence["passed"]
    assert evidence["max_error_m"] > evidence["tolerance_m"]
    assert evidence["max_error_m"] > 0.3 and evidence["sample_count"] > 100
    assert any(n["node_id"] == "meadow" and n["status"] == "blocked" for n in state["node_reports"])
    assert (out / "failure.json").is_file()
    assert not (out / "summary.json").exists()
    assert (previous / "blender/scene.blend").stat().st_mtime_ns == original_mtime


def test_real_dependency_structure_edit_invalidates_only_new_consumer(experiment):
    previous = experiment / "road_edit"
    scene = Scene.from_dict(read(previous / "scene_ir.json"))
    scene.dependencies.append(Dependency("access_track", "remote_meadow", "road_exclusion_buffer", status="validated", input_port="roads"))
    out = experiment / "dependency_edit"
    run_graph(scene, load_graph_config(Path(CONFIG)), out, previous=previous)
    state = read(out / "state.json")
    assert state["passed"] and state["rebuilt_ids"] == ["remote_meadow"]
    assert len(state["preserved_ids"]) == 8
    old_nodes = read(previous / "blender/objects.json")["nodes"]
    new_nodes = read(out / "blender/objects.json")["nodes"]
    for node_id in state["preserved_ids"]:
        if node_id in old_nodes:
            assert old_nodes[node_id] == new_nodes[node_id]
    assert state["artifacts"]["remote_meadow"]["input_bindings"]["roads"] == ["access_track"]


def test_removed_program_node_removes_owned_blender_resources(experiment):
    previous = experiment / "road_edit"
    scene = Scene.from_dict(read(previous / "scene_ir.json"))
    scene.nodes = [n for n in scene.nodes if n.id != "remote_meadow"]
    scene.dependencies = [e for e in scene.dependencies if "remote_meadow" not in (e.source_id, e.target_id)]
    scene.constraints = [c for c in scene.constraints if "remote_meadow" not in c.node_ids]
    out = experiment / "node_removal"
    run_graph(scene, load_graph_config(Path(CONFIG)), out, previous=previous)
    state = read(out / "state.json")
    manifest = read(out / "blender/objects.json")
    assert state["passed"] and state["rebuilt_ids"] == []
    assert len(state["preserved_ids"]) == 8
    assert "remote_meadow" not in state["artifacts"] and "remote_meadow" not in manifest["nodes"]
    assert manifest["removed_backend_ids"] == ["remote_meadow"]


def test_final_actual_geometry_failure_revokes_executable_state(experiment):
    parameters = {"noise_amplitude_m": 1.0, "noise_scale_m": 1.3}
    west, east = [0, 0, 12, 16], [12, 0, 24, 16]
    scene = Scene("unequal_mesh_seam", [
        Node("profile_a", "terrain", "terrain.heightfield", parameters, west),
        Node("profile_b", "terrain", "terrain.heightfield", parameters, east),
        Node("sampled_west", "terrain", "blender.terrain", {"grid_spacing_m": 0.25}, west),
        Node("sampled_east", "terrain", "blender.terrain", {"grid_spacing_m": 2.0}, east),
    ], [
        Dependency("profile_a", "sampled_west", "samples_terrain_height", status="validated", input_port="heightfield"),
        Dependency("profile_b", "sampled_east", "samples_terrain_height", status="validated", input_port="heightfield"),
    ], [Constraint("analytic_seam", "terrain_continuity", ["sampled_west", "sampled_east"],
                   {"tolerance_m": 0.0001, "sample_spacing_m": 0.25})])
    out = experiment / "actual_geometry_failure"
    with pytest.raises((RuntimeError, ValueError)):
        run_graph(scene, load_graph_config(Path(CONFIG)), out)
    state = read(out / "state.json")
    assert not state["passed"] and state["status"] == "failed"
    analytic = next(c for c in state["constraint_reports"] if c["id"] == "analytic_seam")
    assert analytic["passed"], "The negative case must reach the separate actual mesh gate"
    evidence = next(f["check_evidence"] for f in state["failures"]
                    if f.get("check_evidence", {}).get("rule") == "actual_terrain_continuity")
    assert not evidence["passed"] and evidence["max_error_m"] > evidence["tolerance_m"]
    assert evidence["sample_count"] > 2
    assert all(e["status"] != "executable" for e in state["scene"]["dependencies"])
    for node_id in ("sampled_west", "sampled_east"):
        assert state["artifacts"][node_id]["_execution"]["validation_status"] == "failed"
    assert (out / "failure.json").exists() and not (out / "summary.json").exists()

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def run_cli(tmp_path, scene, command):
    (tmp_path / "scene.json").write_text(
        json.dumps(scene.to_dict()), encoding="utf-8"
    )
    env = os.environ.copy()
    source = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = os.pathsep.join(
        [source] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    result = subprocess.run(
        [sys.executable, "-m", "outdoorgraph.cli", command,
         "--scene", "scene.json", "--out", "results"],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    output = tmp_path / "results"
    initial = json.loads((output / "initial.json").read_text(encoding="utf-8"))
    reports = {"initial": initial}
    if command == "demo":
        for name in ("edited", "edited_ir", "summary"):
            reports[name] = json.loads(
                (output / f"{name}.json").read_text(encoding="utf-8")
            )
    return result, reports


@pytest.mark.parametrize("command", ["run", "demo"])
def test_cli_success_writes_reports(tmp_path, demo_scene, command):
    result, reports = run_cli(tmp_path, demo_scene, command)
    assert result.returncode == 0, result.stderr
    assert reports["initial"]["passed"]
    if command == "demo":
        assert reports["edited"]["passed"]
        assert reports["summary"]["initial_passed"]
        assert reports["summary"]["edited_passed"]
        assert reports["summary"]["preserved_after_edit"] == ["background"]
        assert reports["edited_ir"]["nodes"][0]["params"]["local_bump"]["amplitude_m"] == 2.5


@pytest.mark.parametrize("command", ["run", "demo"])
@pytest.mark.parametrize("hard", [True, False])
def test_cli_constraint_failure_exit_status(tmp_path, demo_scene, command, hard):
    demo_scene.get_node("road_main").params["height_offset_m"] = 1.0
    contact = next(c for c in demo_scene.constraints if c.id == "road_on_ground")
    contact.hard = hard
    result, reports = run_cli(tmp_path, demo_scene, command)
    # Terrain sampling has mandatory relation geometry validation. Marking a
    # duplicate Scene contact constraint soft cannot authorize a floating road.
    assert result.returncode == 1, result.stderr
    assert not reports["initial"]["passed"]
    check = next(c for c in reports["initial"]["constraint_reports"]
                 if c["id"] == "road_on_ground")
    assert not check["passed"]
    assert check["hard"] is hard
    if command == "demo":
        assert not reports["summary"]["initial_passed"]
        assert not reports["summary"]["edited_passed"]


@pytest.mark.parametrize("command", ["run", "demo"])
@pytest.mark.parametrize("hard", [True, False])
def test_cli_optional_constraint_failure_exit_status(tmp_path, demo_scene, command, hard):
    population = next(c for c in demo_scene.constraints if c.rule == "vegetation_min_count")
    population.params["min_count"] = 1_000_000
    population.hard = hard
    result, reports = run_cli(tmp_path, demo_scene, command)
    assert result.returncode == (1 if hard else 0), result.stderr
    assert reports["initial"]["passed"] is not hard
    check = next(c for c in reports["initial"]["constraint_reports"] if c["id"] == population.id)
    assert not check["passed"] and check["hard"] is hard
    if command == "demo":
        assert reports["summary"]["initial_passed"] is not hard
        assert reports["summary"]["edited_passed"] is not hard



@pytest.mark.parametrize("hard", [True, False])
def test_cli_edit_only_failure_exit_status(tmp_path, demo_scene, hard):
    grade = next(c for c in demo_scene.constraints if c.id == "road_grade")
    grade.params["max_grade"] = 0.02
    grade.hard = hard
    result, reports = run_cli(tmp_path, demo_scene, "demo")
    assert result.returncode == (1 if hard else 0), result.stderr
    assert reports["initial"]["passed"]
    assert reports["summary"]["initial_passed"]
    assert reports["summary"]["edited_passed"] is not hard
    check = next(c for c in reports["edited"]["constraint_reports"]
                 if c["id"] == "road_grade")
    assert not check["passed"]
    assert check["hard"] is hard

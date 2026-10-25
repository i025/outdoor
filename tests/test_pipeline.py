"""Unit tests for orchestration and rejection boundaries, without model calls.

The synthetic files below are deliberately invalid backend outputs. They are
validator rejection fixtures, never evidence of MoGe or Blender integration.
"""

import json
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from outdoorgraph.adapters import validate_bundle
from outdoorgraph.adapters.pipeline import load_config, make_bundle, stage, verify_outputs


@pytest.fixture
def config_path(tmp_path):
    # Existing paths are sufficient for load_config; none of these placeholders
    # contains a real model or inference source and no backend is launched.
    for name in ("moge/moge/model", "utils3d", "infinigen", "site_packages"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "moge/moge/model/v2.py").write_text("# Config path fixture only.\n")
    (tmp_path / "reference.png").write_bytes(b"configuration-only image placeholder")
    (tmp_path / "weights.pt").write_bytes(b"configuration-only weights placeholder")
    value = {
        "schema_version": "0.2", "seed": 91,
        "input": {"image": "reference.png",
                  "manual_region_polygon_normalized": [[0, 0], [1, 0], [0, 1]]},
        "moge": {"python": sys.executable, "repo": "moge", "utils3d_repo": "utils3d",
                 "weights": "weights.pt"},
        "blender": {"executable": sys.executable, "infinigen_source": "infinigen",
                    "python_site_packages": "site_packages"},
        "vegetation": {"target_instances": 4, "asset_count": 1, "scale": 1.0},
    }
    path = tmp_path / "integration.json"
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def worker_report():
    # Public worker-report contract fixture, not output from model inference.
    return {
        "status": "completed", "face_count": 1, "region_area_m2": 2.0,
        "repo": "/fixture/moge", "commit": "fixture-moge-revision",
        "weights": "/fixture/weights.pt", "model_id": "fixture-only-model",
        "scale_status": "model_estimated_not_surveyed", "bounds_xy": [0, 0, 2, 2],
        "camera": {"id": "reference_camera", "width": 32, "height": 24,
                   "intrinsics_pixels": [[30, 0, 16], [0, 30, 12], [0, 0, 1]],
                   "world_from_camera_cv": [[1, 0, 0, 0], [0, 0, 1, 0],
                                            [0, -1, 0, 0], [0, 0, 0, 1]]},
    }


def test_load_config_resolves_paths_from_config_directory(config_path):
    before = config_path.read_text()
    config = load_config(config_path)
    assert config["input"]["image"] == str(config_path.parent / "reference.png")
    assert config["moge"]["repo"] == str(config_path.parent / "moge")
    assert config["moge"]["python"] == str(Path(sys.executable).resolve())
    assert config_path.read_text() == before


def test_load_config_rejects_wrong_schema(config_path):
    value = json.loads(config_path.read_text())
    value["schema_version"] = "0.1"
    config_path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="schema_version"):
        load_config(config_path)


def test_load_config_rejects_missing_dependency(config_path):
    (config_path.parent / "weights.pt").unlink()
    with pytest.raises(FileNotFoundError, match="moge.weights"):
        load_config(config_path)


def test_load_config_rejects_nonexecutable_binary(config_path):
    binary = config_path.parent / "not_executable"
    binary.write_text("fixture")
    binary.chmod(0o644)
    value = json.loads(config_path.read_text())
    value["blender"]["executable"] = str(binary)
    config_path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="executable"):
        load_config(config_path)


def test_load_config_requires_real_v2_source_layout(config_path):
    (config_path.parent / "moge/moge/model/v2.py").unlink()
    with pytest.raises(FileNotFoundError, match="MoGe v2 source"):
        load_config(config_path)


def test_make_bundle_preserves_declared_dependency_and_manual_provenance(config_path, worker_report):
    config = load_config(config_path)
    bundle = make_bundle(config, worker_report, "fixture-infinigen-revision")
    validate_bundle(bundle)
    surface = bundle.scene.get_node("moge_surface")
    grass = bundle.scene.get_node("infinigen_grass")
    assert surface.operator == "external.moge.mesh"
    assert surface.params["asset_id"] == "moge_mesh"
    assert grass.operator == "external.infinigen.grass"
    assert grass.params["region_id"] == "ground_manual"
    assert grass.params["density"] == 2.0
    assert grass.params["target_instances"] == 4
    assert grass.seed == surface.seed == 91
    assert [(edge.source_id, edge.target_id) for edge in bundle.scene.valid_edges()] == [
        ("moge_surface", "infinigen_grass")]
    assert bundle.regions[0]["source"] == "manual"
    assert bundle.provenance["automatic_region_inference"] is False
    assert bundle.provenance["moge"]["commit"] == worker_report["commit"]
    assert bundle.provenance["infinigen"]["commit"] == "fixture-infinigen-revision"
    assert bundle.scene.schema_version == "0.1"


@pytest.mark.parametrize("change", [{"status": "failed"}, {"face_count": 0}])
def test_make_bundle_rejects_incomplete_worker_result(config_path, worker_report, change):
    worker_report.update(change)
    with pytest.raises(ValueError, match="completed surface"):
        make_bundle(load_config(config_path), worker_report, "fixture-revision")


@pytest.mark.parametrize("area", [0, -1, float("nan"), float("inf")])
def test_make_bundle_rejects_bad_manual_area(config_path, worker_report, area):
    worker_report["region_area_m2"] = area
    with pytest.raises(ValueError, match="region area"):
        make_bundle(load_config(config_path), worker_report, "fixture-revision")


@pytest.mark.parametrize("target", [0, 2001])
def test_make_bundle_rejects_unbounded_instance_request(config_path, worker_report, target):
    config = load_config(config_path)
    config["vegetation"]["target_instances"] = target
    with pytest.raises(ValueError, match="scatter count"):
        make_bundle(config, worker_report, "fixture-revision")


def test_make_bundle_rejects_excessive_density(config_path, worker_report):
    worker_report["region_area_m2"] = 0.001
    with pytest.raises(ValueError, match="density"):
        make_bundle(load_config(config_path), worker_report, "fixture-revision")


def test_stage_records_real_stdlib_process_success(tmp_path):
    records = []
    stage("fixture_success", [sys.executable, "-c", "print('unit process only')"],
          tmp_path, os.environ.copy(), tmp_path, records, timeout=10)
    assert records[0]["returncode"] == 0
    assert records[0]["seconds"] >= 0
    assert "unit process only" in (tmp_path / "fixture_success.log").read_text()
    assert json.loads((tmp_path / "execution.json").read_text()) == records


def test_stage_propagates_nonzero_exit_and_keeps_log(tmp_path):
    records = []
    command = [sys.executable, "-c", "print('partial output', flush=True); raise SystemExit(7)"]
    with pytest.raises(RuntimeError, match="exit 7"):
        stage("fixture_failure", command, tmp_path, os.environ.copy(), tmp_path, records)
    assert records[0]["returncode"] == 7
    assert records[0]["argv"] == command
    assert "partial output" in (tmp_path / "fixture_failure.log").read_text()
    assert json.loads((tmp_path / "execution.json").read_text()) == records


def test_stage_propagates_timeout_and_keeps_execution_record(tmp_path):
    records = []
    with pytest.raises(subprocess.TimeoutExpired):
        stage("fixture_timeout", [sys.executable, "-c", "import time; time.sleep(5)"],
              tmp_path, os.environ.copy(), tmp_path, records, timeout=0.2)
    assert records[0]["timed_out"] is True
    assert records[0]["seconds"] >= 0
    assert json.loads((tmp_path / "execution.json").read_text()) == records


def test_stage_propagates_missing_executable(tmp_path):
    records = []
    with pytest.raises(FileNotFoundError):
        stage("fixture_missing", [str(tmp_path / "missing_binary")],
              tmp_path, os.environ.copy(), tmp_path, records)
    assert json.loads((tmp_path / "execution.json").read_text()) == records
    assert records[0]["seconds"] >= 0


def write_header_rejection_fixture(out):
    """Fabricated headers must never qualify as successful backend outputs."""
    backend = out / "blender"
    backend.mkdir()
    manifest = {
        "scene_id": "validator_rejection_fixture", "vegetation_instance_count": 1,
        "camera_projection_max_error_pixels": 0.0,
        "render_width": 2, "render_height": 2, "device": "unit-test-placeholder",
        "source_factory": "infinigen.assets.grassland.grass_tuft.GrassTuftFactory",
        "objects": [{"id": "surface", "node_id": "moge_surface"},
                    {"id": "grass", "node_id": "infinigen_grass"}],
        "vegetation_instances": [{"id": "grass:instance:0", "og_node_id": "infinigen_grass",
                                  "position_xyz": [0, 0, 0]}],
    }
    (backend / "objects.json").write_text(json.dumps(manifest))
    (backend / "scene.blend").write_bytes(b"BLENDER")
    (backend / "scene.glb").write_bytes(struct.pack("<4sII", b"glTF", 2, 12))
    png_header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 2, 2)
    for name in ("render.png", "overview.png"):
        (backend / name).write_bytes(png_header)
    return backend, manifest


def test_verify_outputs_rejects_missing_artifacts(tmp_path):
    with pytest.raises((FileNotFoundError, ValueError)):
        verify_outputs(tmp_path)


def test_verify_outputs_rejects_header_only_backend_files(tmp_path):
    write_header_rejection_fixture(tmp_path)
    with pytest.raises(ValueError):
        verify_outputs(tmp_path)


def test_verify_outputs_rejects_empty_scene_file(tmp_path):
    backend, _ = write_header_rejection_fixture(tmp_path)
    (backend / "scene.blend").write_bytes(b"")
    with pytest.raises(ValueError):
        verify_outputs(tmp_path)


def test_verify_outputs_rejects_malformed_manifest(tmp_path):
    backend = tmp_path / "blender"
    backend.mkdir()
    (backend / "objects.json").write_text("{")
    with pytest.raises(ValueError):
        verify_outputs(tmp_path)


def test_verify_outputs_rejects_empty_vegetation(tmp_path):
    backend, manifest = write_header_rejection_fixture(tmp_path)
    manifest["vegetation_instance_count"] = 0
    manifest["vegetation_instances"] = []
    (backend / "objects.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="instance|vegetation|Infinigen"):
        verify_outputs(tmp_path)


@pytest.mark.parametrize("error", [float("nan"), float("inf"), -0.1])
def test_verify_outputs_rejects_invalid_camera_error_before_files(tmp_path, error):
    backend, manifest = write_header_rejection_fixture(tmp_path)
    manifest["camera_projection_max_error_pixels"] = error
    (backend / "objects.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="camera|Camera|calibration|finite"):
        verify_outputs(tmp_path)


def test_verify_outputs_rejects_instance_count_mismatch_before_files(tmp_path):
    backend, manifest = write_header_rejection_fixture(tmp_path)
    manifest["vegetation_instance_count"] = 99
    (backend / "objects.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="instance|Instance|count"):
        verify_outputs(tmp_path)

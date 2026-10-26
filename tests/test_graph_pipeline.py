"""Launcher tests. Real Blender checks live in the explicit opt-in suite."""
import json
from pathlib import Path
import sys

import pytest

from outdoorgraph.adapters.graph_pipeline import load_graph_config, write_graph_visualization
from outdoorgraph.ir import Dependency, Node, Scene


def test_graph_config_resolves_independent_backend_paths(tmp_path):
    (tmp_path / "infinigen").mkdir()
    (tmp_path / "site-packages").mkdir()
    source = {"blender": {"executable": sys.executable, "infinigen_source": "infinigen",
                          "python_site_packages": "site-packages"}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(source))
    result = load_graph_config(path)
    assert result["blender"]["infinigen_source"] == str(tmp_path / "infinigen")
    assert json.loads(path.read_text()) == source


def test_graph_config_missing_backend_is_explicit(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"blender": {"executable": str(tmp_path / "absent")}}))
    with pytest.raises(FileNotFoundError, match="blender.executable"):
        load_graph_config(path)


def test_propagation_visualization_uses_execution_report_and_arbitrary_ids(tmp_path):
    scene = Scene("renamed", [Node("height", "terrain", "terrain.heightfield"),
                             Node("path", "road", "road.polyline")],
                  [Dependency("height", "path", "samples_terrain_height", status="executable")])
    write_graph_visualization(scene, {"rebuilt_ids": ["path"], "preserved_ids": ["height"]}, tmp_path)
    graph = json.loads((tmp_path / "dependency_graph.json").read_text())
    assert {item["id"]: item["updated"] for item in graph["nodes"]} == {"height": False, "path": True}
    assert graph["dependencies"][0]["status"] == "executable"
    svg = (tmp_path / "dependency_graph.svg").read_text()
    assert "height" in svg and "path" in svg and 'marker-end="url(#arrow)"' in svg

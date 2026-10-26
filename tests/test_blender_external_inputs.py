"""Opt-in real Blender cache tests using a tiny MoGe-format NPZ fixture.

The fixture is authored geometry, not an additional MoGe inference claim.
"""
import copy
import json
import os
from pathlib import Path
import subprocess

import pytest

from outdoorgraph.adapters.graph_pipeline import load_graph_config
from outdoorgraph.ir import Dependency, Node, Scene

CONFIG = os.environ.get("OUTDOORGRAPH_BLENDER_CONFIG")
pytestmark = pytest.mark.skipif(not CONFIG, reason="Set OUTDOORGRAPH_BLENDER_CONFIG for real Blender")

FIXTURE_SCRIPT = r'''
import bpy,json,numpy as np,sys
from pathlib import Path
args=sys.argv[sys.argv.index('--')+1:];mode=args[0];root=Path(args[1])
def image(name,color):
    value=bpy.data.images.new(name,width=8,height=8)
    value.pixels=list(color)*64
    value.filepath_raw=str(root/name);value.file_format='PNG';value.save()
if mode=='prepare':
    root.mkdir(parents=True,exist_ok=True)
    for name,x in [('main',0),('other',6)]:
        np.savez(root/(name+'.npz'),vertices=np.array([[x,0,0],[x+4,0,0],[x+4,4,0],[x,4,0]],float),
                 faces=np.array([[0,1,2],[0,2,3]]),uv=np.array([[0,1],[1,1],[1,0],[0,0]],float),
                 vertex_colors=np.ones((4,3)),region_weight=np.ones(4))
        image(name+'.png',(1,0,0,1))
elif mode=='texture':image('main.png',(0,0,1,1))
elif mode=='geometry':
    with np.load(root/'main.npz') as data:values={key:data[key].copy() for key in data.files}
    values['vertices'][:,2]+=.5;np.savez(root/'main.npz',**values)
elif mode=='audit':
    bpy.ops.wm.open_mainfile(filepath=args[2]);result={}
    for obj in bpy.data.objects:
        if obj.get('og_role')!='terrain':continue
        textures=[n.image for mat in obj.data.materials for n in mat.node_tree.nodes if n.type=='TEX_IMAGE']
        result[obj['og_node_id']]={'pixels':list(textures[0].pixels[:4]),'image_name':textures[0].name,
            'image_owner':textures[0].get('og_node_id'),'packed':len(textures[0].packed_files),
            'z_values':[v.co.z for v in obj.data.vertices]}
    Path(args[3]).write_text(json.dumps(result))
'''


def read(path):
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def external_experiment(tmp_path_factory):
    base = Path(os.environ["OUTDOORGRAPH_EXTERNAL_OUTPUT"]) if os.environ.get("OUTDOORGRAPH_EXTERNAL_OUTPUT") else tmp_path_factory.mktemp("external_inputs")
    base.mkdir(parents=True, exist_ok=True)
    config = load_graph_config(Path(CONFIG))["blender"]
    source = Path(__file__).resolve().parents[1] / "src"
    worker = source / "outdoorgraph/adapters/graph_blender_worker.py"
    fixture = base / "fixture.py"
    fixture.write_text(FIXTURE_SCRIPT)
    env = os.environ.copy()
    env.update(PYTHONPATH=os.pathsep.join([str(source), config["python_site_packages"], config["infinigen_source"]]),
               PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0", OMP_NUM_THREADS="4", OPENBLAS_NUM_THREADS="4")
    for key in ("BLENDER_USER_RESOURCES", "XDG_CACHE_HOME", "MPLCONFIGDIR"):
        folder = base / "cache" / key
        folder.mkdir(parents=True, exist_ok=True)
        env[key] = str(folder.resolve())
    command = [config["executable"], "--background", "-noaudio", "--threads", "4",
               "--python-use-system-env", "--python-exit-code", "1", "--python"]

    def process(script, args, log, success=True):
        with log.open("w") as stream:
            result = subprocess.run(command + [str(script), "--"] + list(map(str, args)),
                                    env=env, stdout=stream, stderr=subprocess.STDOUT)
        assert (result.returncode == 0) == success, log.read_text()[-5000:]

    resources = base / "assets"
    process(fixture, ["prepare", resources], base / "prepare.log")
    scene = Scene("external_fixture", [
        Node("surface_main", "terrain", "external.moge.mesh", {"asset_id": "main_mesh", "source_image_asset_id": "main_image"}, [0, 0, 4, 4]),
        Node("surface_other", "terrain", "external.moge.mesh", {"asset_id": "other_mesh", "source_image_asset_id": "other_image"}, [6, 0, 10, 4]),
        Node("native_plants", "vegetation", "external.infinigen.grass", {"density": 2, "asset_count": 1, "scale": 1}, [0, 0, 4, 4], seed=19),
        Node("sky", "background", "background.constant"),
    ], [Dependency("surface_main", "native_plants", "scatter_on_reconstructed_surface", status="validated", input_port="terrain")])
    bundle = {"scene": scene.to_dict(), "assets": [
        {"id": name+"_"+kind, "uri": name+extension}
        for name in ("main", "other") for kind, extension in (("mesh", ".npz"), ("image", ".png"))],
        "regions": [], "camera": {"id": "fixture_camera", "width": 64, "height": 64,
        "intrinsics_pixels": [[50, 0, 32], [0, 50, 32], [0, 0, 1]],
        "world_from_camera_cv": [[1, 0, 0, 2], [0, -1, 0, 2], [0, 0, -1, 8], [0, 0, 0, 1]]}}
    request = {"scene": scene.to_dict(), "bundle": bundle, "bundle_path": str(resources / "bundle.json"),
               "render": {"width": 128, "height": 128, "samples": 2}, "previous_artifacts": {}, "previous_blend": None}
    previous = None
    for name in ("baseline", "unchanged", "texture", "geometry", "missing"):
        out = base / name
        out.mkdir()
        if name in ("texture", "geometry"):
            process(fixture, [name, resources], out / "edit.log")
        elif name == "missing":
            (resources / "main.npz").unlink()
        current = copy.deepcopy(request)
        if previous:
            current["previous_artifacts"] = read(previous / "state.json")["artifacts"]
            current["previous_blend"] = str(previous / "scene.blend")
        (out / "input.json").write_text(json.dumps(current))
        process(worker, ["--request", out / "input.json", "--out", out,
                         "--infinigen-source", config["infinigen_source"]], out / "graph.log", name != "missing")
        if name != "missing":
            process(worker, ["--verify", out, "--infinigen-source", config["infinigen_source"]], out / "reopen.log")
            process(fixture, ["audit", resources, out / "scene.blend", out / "image_audit.json"], out / "audit.log")
            previous = out
    return base


def test_real_same_path_texture_reloads_pixels_and_preserves_independent_surface(external_experiment):
    base = external_experiment
    unchanged = read(base / "unchanged/state.json")
    assert unchanged["passed"] and unchanged["rebuilt_ids"] == []
    changed = read(base / "texture/state.json")
    assert changed["passed"] and changed["rebuilt_ids"] == ["surface_main", "native_plants"]
    old, new = read(base / "baseline/image_audit.json"), read(base / "texture/image_audit.json")
    assert old["surface_main"]["pixels"] == [1, 0, 0, 1]
    assert new["surface_main"]["pixels"] == [0, 0, 1, 1]
    assert new["surface_main"]["packed"] > 0 and new["surface_main"]["image_owner"] == "surface_main"
    assert old["surface_other"] == new["surface_other"]
    before, after = read(base / "baseline/objects.json"), read(base / "texture/objects.json")
    assert before["nodes"]["surface_other"] == after["nodes"]["surface_other"]


def test_real_same_path_npz_rebuilds_actual_mesh_and_native_grass(external_experiment):
    base = external_experiment
    state = read(base / "geometry/state.json")
    assert state["passed"] and state["rebuilt_ids"] == ["surface_main", "native_plants"]
    audit = read(base / "geometry/image_audit.json")
    assert audit["surface_main"]["z_values"] == [.5] * 4
    assert audit["surface_other"]["z_values"] == [0] * 4
    positions = read(base / "geometry/objects.json")["nodes"]["native_plants"]["instances"]
    assert positions and all(abs(item["position_xyz"][2] - .5) < 1e-5 for item in positions)
    assert read(base / "geometry/reopen_verification.json")["passed"]


def test_real_missing_npz_fails_safely_before_cached_reuse(external_experiment):
    state = read(external_experiment / "missing/state.json")
    assert not state["passed"] and "surface_main" not in state["artifacts"]
    assert "native_plants" not in state["artifacts"]
    assert set(state["preserved_ids"]) == {"surface_other", "sky"}
    assert any(f["node_id"] == "surface_main" and f["phase"] == "external_inputs" for f in state["failures"])
    assert not (external_experiment / "missing/scene.blend").exists()

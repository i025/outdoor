"""File-based orchestration of isolated, existing MoGe and Infinigen backends."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import struct
import subprocess
import time
from typing import Any
import zipfile
import zlib

from outdoorgraph.adapters.contracts import SceneBundle, resolve_uri
from outdoorgraph.ir import Dependency, Evidence, Node, Scene


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != "0.2":
        raise ValueError("Integration config requires schema_version 0.2")
    references = [("input", "image"), ("moge", "python"), ("moge", "repo"),
                  ("moge", "utils3d_repo"), ("moge", "weights"),
                  ("blender", "executable"), ("blender", "infinigen_source"),
                  ("blender", "python_site_packages")]
    for section, key in references:
        target = resolve_uri(path.parent, config[section][key])
        if not target.exists():
            raise FileNotFoundError(f"Missing {section}.{key}: {target}")
        config[section][key] = str(target)
    for section, key in (("moge", "python"), ("blender", "executable")):
        if not os.access(config[section][key], os.X_OK):
            raise ValueError(f"Not executable: {section}.{key}")
    script = Path(config["moge"]["repo"]) / "moge/model/v2.py"
    if not script.is_file():
        raise FileNotFoundError(f"Expected existing MoGe v2 source: {script}")
    for section, key, default, maximum in (("moge", "resize", 512, None),
            ("geometry", "stride", 4, None), ("vegetation", "asset_count", 2, 16),
            ("vegetation", "target_instances", 80, 2000)):
        value = config.get(section, {}).get(key, default)
        if type(value) is not int or value < 1 or (maximum and value > maximum):
            raise ValueError(f"{section}.{key} must be a bounded positive integer")
    for section, key, default in (("geometry", "scale_m_per_unit", 1.0),
                                  ("vegetation", "scale", 2.0)):
        value = config.get(section, {}).get(key, default)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{section}.{key} must be finite and positive")
    polygon = config["input"].get("manual_region_polygon_normalized", [])
    if (len(polygon) < 3 or any(not isinstance(p, list) or len(p) != 2 or
            any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in p)
            for p in polygon)):
        raise ValueError("A normalized manual region polygon is required")
    return config


def make_bundle(config: dict[str, Any], report: dict[str, Any], backend_commit: str) -> SceneBundle:
    """Translate real worker results to editable IR plus external resource references."""
    if report.get("status") != "completed" or report.get("face_count", 0) <= 0:
        raise ValueError("MoGe worker did not produce a completed surface")
    area = float(report["region_area_m2"])
    target = int(config.get("vegetation", {}).get("target_instances", 80))
    if not math.isfinite(area) or area <= 0 or not 1 <= target <= 2000:
        raise ValueError("Invalid manual region area or requested scatter count")
    density = target / area
    if not 0 < density <= 100:
        raise ValueError("Manual area requires unsafe density; use a larger region or fewer instances")
    seed = int(config.get("seed", 2026))
    provenance = {"moge": {key: report[key] for key in ("repo", "commit", "weights", "model_id")},
                  "infinigen": {"repo": config["blender"]["infinigen_source"], "commit": backend_commit},
                  "scale_status": report["scale_status"],
                  "automatic_region_inference": False,
                  "manual_inputs": ["ground region polygon", "level-camera world orientation"],
                  "unimplemented": ["automatic region segmentation", "program induction",
                                    "image-inferred causal dependencies", "global consistency optimization"]}
    scene = Scene(
        id="moge_infinigen_mountain_v02",
        nodes=[
            Node("moge_surface", "terrain", "external.moge.mesh",
                 params={"asset_id": "moge_mesh", "source_image_asset_id": "reference_image",
                         "scale_m_per_unit": config.get("geometry", {}).get("scale_m_per_unit", 1.0),
                         "mesh_stride": config.get("geometry", {}).get("stride", 4)},
                 bounds_xy=report["bounds_xy"], seed=seed, evidence_ids=["reference_image_evidence"],
                 metadata={"provenance": provenance["moge"], "inference": "official MoGe v2 API",
                           "scale_status": report["scale_status"]}),
            Node("infinigen_grass", "vegetation", "external.infinigen.grass",
                 params={"surface_node_id": "moge_surface", "region_id": "ground_manual",
                         "factory": "infinigen.assets.grassland.grass_tuft.GrassTuftFactory",
                         "density": density, "target_instances": target,
                         "asset_count": int(config.get("vegetation", {}).get("asset_count", 2)),
                         "scale": float(config.get("vegetation", {}).get("scale", 2.0))},
                 bounds_xy=report["bounds_xy"], seed=seed,
                 metadata={"provenance": provenance["infinigen"], "region_source": "manual",
                           "dependency_origin": "declared adapter contract, not inferred from image"}),
        ],
        # The isolated Blender adapter explicitly validates this proposal against
        # its registered contracts before execution; model confidence grants no permit.
        dependencies=[Dependency("moge_surface", "infinigen_grass", "scatter_on_reconstructed_surface",
                                 status="proposed")],
        evidence=[Evidence("reference_image_evidence", "image", config["input"]["image"])],
    )
    bundle = SceneBundle(
        scene=scene, camera=report["camera"], provenance=provenance,
        assets=[{"id": "moge_mesh", "uri": "geometry/geometry.npz", "format": "npz_triangle_mesh",
                 "coordinate_frame": "world_z_up", "provenance": provenance["moge"]},
                {"id": "reference_image", "uri": "geometry/reference.png", "format": "png",
                 "coordinate_frame": "image_uv_top_left", "provenance": {"source": config["input"]["image"]}}],
        regions=[{"id": "ground_manual", "uri": "geometry/ground_manual.png", "semantic": "candidate_grass_region",
                  "source": "manual", "width": report["camera"]["width"], "height": report["camera"]["height"],
                  "polygon_normalized": config["input"]["manual_region_polygon_normalized"]}],
    )
    return bundle


def stage(name: str, command: list[str], cwd: Path, env: dict[str, str], out: Path,
          records: list[dict[str, Any]], timeout: int = 900) -> None:
    """Run an isolated backend and preserve failures, exact argv and complete logs."""
    started = time.monotonic()
    record = {"stage": name, "argv": command, "cwd": str(cwd), "log": name+".log"}
    records.append(record)
    try:
        with (out / (name+".log")).open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=timeout, check=False)
        record["returncode"] = result.returncode
        if result.returncode:
            raise RuntimeError(f"{name} failed with exit {result.returncode}; see {out / (name+'.log')}")
    except subprocess.TimeoutExpired:
        record["timed_out"] = True
        raise
    finally:
        record["seconds"] = time.monotonic() - started
        write_json(out / "execution.json", records)


def png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Invalid rendered PNG: " + str(path))
    offset, compressed, dimensions, ended = 8, bytearray(), None, False
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset:offset+4])[0]
        kind = data[offset+4:offset+8]
        payload = data[offset+8:offset+8+length]
        end = offset+8+length
        if end+4 > len(data) or (zlib.crc32(kind+payload) & 0xffffffff) != struct.unpack(">I", data[end:end+4])[0]:
            raise ValueError("Corrupt PNG chunk: " + str(path))
        if kind == b"IHDR":
            if dimensions is not None or length != 13:
                raise ValueError("Invalid PNG IHDR")
            dimensions = struct.unpack(">II", payload[:8])
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            ended = True
            break
        offset = end+4
    if not dimensions or not all(dimensions) or not ended or not compressed or not zlib.decompress(compressed):
        raise ValueError("Incomplete rendered PNG: " + str(path))
    return dimensions


def verify_outputs(out: Path) -> dict[str, Any]:
    """Validate actual backend files; a process returning zero alone is insufficient."""
    backend = out / "blender"
    manifest = json.loads((backend / "objects.json").read_text())
    count = manifest.get("vegetation_instance_count", 0)
    if type(count) is not int or count <= 0:
        raise ValueError("No actual Infinigen instances were generated")
    error = manifest.get("camera_projection_max_error_pixels", float("inf"))
    if type(error) not in (int, float) or not math.isfinite(error) or not 0 <= error <= 1e-3:
        raise ValueError("Blender camera calibration did not match the bundle")
    instances = manifest.get("vegetation_instances", [])
    if len(instances) != count or len({i["id"] for i in instances}) != count:
        raise ValueError("Actual instance count or IDs disagree with manifest")
    blend = backend / "scene.blend"
    with blend.open("rb") as f:
        header = f.read(12)
        if (len(header) != 12 or header[:7] != b"BLENDER" or header[7:8] not in (b"-", b"_")
                or header[8:9] not in (b"v", b"V") or not header[9:].isdigit()
                or blend.stat().st_size < 1024):
            raise ValueError("Invalid uncompressed Blender file")
    glb = backend / "scene.glb"
    with glb.open("rb") as f:
        header = f.read(12)
    if len(header) != 12 or struct.unpack("<4sII", header) != (b"glTF", 2, glb.stat().st_size):
        raise ValueError("Invalid GLB header/length")
    with glb.open("rb") as f:
        f.seek(12)
        chunk_header = f.read(8)
        if len(chunk_header) != 8:
            raise ValueError("GLB is missing its scene JSON chunk")
        length, kind = struct.unpack("<I4s", chunk_header)
        if kind != b"JSON" or length > glb.stat().st_size-20:
            raise ValueError("Invalid GLB scene chunk")
        gltf = json.loads(f.read(length))
        if not gltf.get("meshes") or not gltf.get("buffers"):
            raise ValueError("GLB contains no actual geometry")
    for name in ("render.png", "overview.png"):
        width, height = png_dimensions(backend / name)
        if (width, height) != (manifest["render_width"], manifest["render_height"]):
            raise ValueError("Render dimensions disagree with backend manifest")
    bundle = SceneBundle.load(out / "scene_bundle.json")
    surface_ids = {node.id for node in bundle.scene.nodes if node.operator == "external.moge.mesh"}
    ids = [o["id"] for o in manifest["objects"]]
    if len(ids) != len(set(ids)) or not any(o["node_id"] in surface_ids for o in manifest["objects"]):
        raise ValueError("Missing or duplicate stable object IDs")
    saved_scene = Scene.from_dict(json.loads((out / "scene_ir.json").read_text()))
    if saved_scene.to_dict() != bundle.scene.to_dict():
        raise ValueError("Scene IR differs from the execution bundle")
    with zipfile.ZipFile(out / "geometry/moge_raw.npz") as raw:
        if not {"points.npy", "depth.npy", "intrinsics.npy", "mask.npy"}.issubset(raw.namelist()) or raw.testzip():
            raise ValueError("MoGe raw output is incomplete or corrupt")
    reopened = json.loads((backend / "reopen_verification.json").read_text())
    if reopened.get("status") != "passed":
        raise ValueError("Blender scene/GLB failed independent reopen verification")
    return {"status": "completed", "scene_id": manifest["scene_id"],
            "vegetation_instances": manifest["vegetation_instance_count"],
            "camera_projection_max_error_pixels": manifest["camera_projection_max_error_pixels"],
            "source_factory": manifest["source_factory"], "render_device": manifest["device"],
            "automatic_region_inference": False,
            "outputs": ["scene_ir.json", "scene_bundle.json", "geometry/moge_raw.npz",
                        "blender/scene.blend", "blender/scene.glb", "blender/render.png",
                        "blender/overview.png", "blender/objects.json"],
            "limitations": ["manual mask and declared world orientation", "estimated monocular scale",
                            "single-view 2.5D surface; unseen geometry is not reconstructed",
                            "image texture comes from reference, not a learned procedural material",
                            "no mechanism induction or global optimization in v0.2"]}


def run_integration(config_path: Path, out: Path) -> dict[str, Any]:
    config = load_config(config_path.resolve())
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "config.resolved.json", config)
    records: list[dict[str, Any]] = []
    workers = Path(__file__).resolve().parent
    env = os.environ.copy()
    cache = out / "cache"
    for key, subdirectory in {"XDG_CACHE_HOME": "xdg", "TORCH_HOME": "torch", "HF_HOME": "hf",
                              "MPLCONFIGDIR": "matplotlib", "CUDA_CACHE_PATH": "cuda",
                              "BLENDER_USER_RESOURCES": "blender"}.items():
        folder = cache / subdirectory
        folder.mkdir(parents=True, exist_ok=True)
        env[key] = str(folder)
    env.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="4", OPENBLAS_NUM_THREADS="4",
               HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    env["CUDA_VISIBLE_DEVICES"] = str(config["moge"].get("cuda_visible_devices", "0"))
    try:
        print("Running existing MoGe model; log:", out / "moge.log", flush=True)
        stage("moge", [config["moge"]["python"], str(workers / "moge_worker.py"),
                        "--config", str(out / "config.resolved.json"), "--out", str(out / "geometry")],
              out, env, out, records)
        report = json.loads((out / "geometry/moge_report.json").read_text())
        commit = subprocess.check_output(["git", "-C", config["blender"]["infinigen_source"],
                                          "rev-parse", "HEAD"], text=True).strip()
        bundle = make_bundle(config, report, commit)
        bundle.save(out / "scene_bundle.json")
        write_json(out / "scene_ir.json", bundle.scene.to_dict())
        env["PYTHONPATH"] = os.pathsep.join([config["blender"]["python_site_packages"],
                                             config["blender"]["infinigen_source"]])
        print("Running native Infinigen generation and Blender render; log:", out / "blender.log", flush=True)
        stage("blender", [config["blender"]["executable"], "--background", "-noaudio", "--threads", "4",
                           "--python-use-system-env", "--python-exit-code", "1",
                           "--python", str(workers / "blender_worker.py"), "--",
                           "--bundle", str(out / "scene_bundle.json"), "--out", str(out / "blender"),
                           "--infinigen-source", config["blender"]["infinigen_source"]],
              out, env, out, records)
        print("Reopening scene and GLB for independent geometry/metadata checks", flush=True)
        stage("reopen", [config["blender"]["executable"], "--background", str(out / "blender/scene.blend"),
                          "-noaudio", "--threads", "4", "--python-use-system-env", "--python-exit-code", "1",
                          "--python", str(workers / "blender_verify.py"), "--",
                          "--bundle", str(out / "scene_bundle.json"), "--out", str(out / "blender")],
              out, env, out, records)
        summary = verify_outputs(out)
        write_json(out / "summary.json", summary)
        print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
        return summary
    except Exception as error:
        write_json(out / "failure.json", {"status": "failed", "error": str(error), "execution": records})
        raise

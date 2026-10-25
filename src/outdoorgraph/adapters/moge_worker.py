"""Thin serializer around the existing MoGe v2 public inference API.

Run with the external MoGe Python, never with OutdoorGraph's core environment.
No reconstruction or vegetation algorithm is implemented here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import warnings


def make_mesh(points, valid, rgb, region, stride, edge_ratio):
    import numpy as np

    h, w = valid.shape
    yy, xx = np.meshgrid(np.arange(0, h, stride), np.arange(0, w, stride), indexing="ij")
    sampled = points[yy, xx]
    keep = valid[yy, xx] & np.isfinite(sampled).all(axis=-1) & (sampled[..., 2] > 0)
    # Decimation may not bridge an invalid pixel/edge hidden between samples.
    invalid = ~valid | ~np.isfinite(points).all(axis=-1) | (points[..., 2] <= 0)
    integral = np.pad(invalid.astype(np.int64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    rows, cols = yy[:, 0], xx[0]
    cell_invalid = (integral[np.ix_(rows[1:]+1, cols[1:]+1)]
                    - integral[np.ix_(rows[:-1], cols[1:]+1)]
                    - integral[np.ix_(rows[1:]+1, cols[:-1])]
                    + integral[np.ix_(rows[:-1], cols[:-1])])
    cell_valid = cell_invalid == 0
    index = np.full(keep.shape, -1, dtype=np.int32)
    index[keep] = np.arange(int(keep.sum()))
    a, b, c, d = index[:-1, :-1], index[:-1, 1:], index[1:, :-1], index[1:, 1:]
    candidates = np.concatenate([np.stack([a, c, b], -1)[cell_valid].reshape(-1, 3),
                                 np.stack([b, c, d], -1)[cell_valid].reshape(-1, 3)])
    candidates = candidates[(candidates >= 0).all(axis=1)]
    vertices_cv = sampled[keep].astype(np.float32)
    depths = vertices_cv[candidates, 2]
    candidates = candidates[(depths.max(axis=1) - depths.min(axis=1)) /
                            depths.min(axis=1) <= edge_ratio]
    if not len(candidates):
        raise ValueError("No valid MoGe triangles after discontinuity filtering")
    if not len(candidates):
        raise ValueError("No valid MoGe triangles after discontinuity filtering")
    uv = np.stack([(xx[keep] + 0.5) / w, (yy[keep] + 0.5) / h], axis=1).astype(np.float32)
    colors = rgb[yy[keep], xx[keep]].astype(np.float32) / 255.0
    weights = region[yy[keep], xx[keep]].astype(np.float32)
    used, inverse = np.unique(candidates, return_inverse=True)
    return (vertices_cv[used], inverse.reshape(-1, 3).astype(np.int32),
            uv[used], colors[used], weights[used])


def run(config_path: Path, output: Path):
    import numpy as np
    from PIL import Image, ImageDraw, ImageOps
    import torch

    config = json.loads(config_path.read_text(encoding="utf-8"))
    settings = config["moge"]
    sys.path.insert(0, str(Path(settings["utils3d_repo"]).resolve()))
    sys.path.insert(0, str(Path(settings["repo"]).resolve()))
    from moge.model.v2 import MoGeModel

    if settings.get("version", "v2") != "v2":
        raise ValueError("This adapter explicitly supports the verified MoGe v2 checkpoint only")
    geometry = config.get("geometry", {})
    stride = int(geometry.get("stride", 4))
    edge_ratio = float(geometry.get("max_edge_depth_ratio", 0.25))
    scale = float(geometry.get("scale_m_per_unit", 1.0))
    if stride < 1 or not np.isfinite(scale) or scale <= 0 or not 0 < edge_ratio <= 1:
        raise ValueError("Invalid geometry stride, scale, or discontinuity threshold")
    output.mkdir(parents=True, exist_ok=True)
    image_path = Path(config["input"]["image"]).resolve()
    original = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
    image = original.copy()
    image.thumbnail((settings.get("resize", 512), settings.get("resize", 512)))
    rgb = np.asarray(image).copy()
    width, height = image.size
    torch.set_num_threads(4)
    torch.manual_seed(int(config.get("seed", 2026)))
    device = settings.get("device", "cuda:0")
    started = time.monotonic()
    with warnings.catch_warnings(record=True) as caught:
        model = MoGeModel.from_pretrained(str(Path(settings["weights"]).resolve())).to(device).eval()
    load_warnings = [str(item.message) for item in caught]
    if any("absent from the checkpoint" in message or "no counterpart" in message
           for message in load_warnings):
        raise ValueError("MoGe checkpoint does not match the selected model: " + repr(load_warnings))
    tensor = torch.from_numpy(rgb).permute(2, 0, 1).float().to(device) / 255.0
    if device.startswith("cuda"):
        torch.cuda.reset_peak_memory_stats()
    before_infer = time.monotonic()
    with torch.inference_mode():
        prediction = model.infer(tensor, resolution_level=settings.get("resolution_level", 0),
                                 apply_mask=False)
    if device.startswith("cuda"):
        torch.cuda.synchronize()
    inference_seconds = time.monotonic() - before_infer
    arrays = {key: value.detach().cpu().numpy() for key, value in prediction.items()}
    points = arrays["points"]
    valid = arrays["mask"].astype(bool) & np.isfinite(points).all(axis=-1) & (points[..., 2] > 0)
    if points.shape != (height, width, 3) or not valid.any():
        raise ValueError("Invalid MoGe point-map dimensions or empty validity mask")
    arrays["mask"] = valid
    np.savez_compressed(output / "moge_raw.npz", **arrays)
    image.save(output / "reference.png")
    Image.fromarray(valid.astype(np.uint8) * 255).save(output / "moge_validity.png")

    polygon = config["input"]["manual_region_polygon_normalized"]
    if len(polygon) < 3 or any(len(p) != 2 or not all(0 <= v <= 1 for v in p) for p in polygon):
        raise ValueError("A normalized manual region polygon with at least three points is required")
    region_image = Image.new("L", image.size)
    ImageDraw.Draw(region_image).polygon([(round(x*(width-1)), round(y*(height-1)))
                                         for x, y in polygon], fill=255)
    region_image.save(output / "ground_manual.png")
    region = np.asarray(region_image) > 0
    try:
        import utils3d_moge as utils3d
    except ImportError:
        import utils3d
    # Reuse the official MoGe CLI's existing depth-discontinuity detector.
    mesh_valid = valid & ~utils3d.np.depth_map_edge(arrays["depth"], rtol=edge_ratio)
    vertices_cv, faces, uv, colors, weights = make_mesh(points, mesh_valid, rgb, region, stride, edge_ratio)
    # OpenCV (right, down, forward) -> right-handed world (right, forward, up).
    vertices = vertices_cv[:, [0, 2, 1]] * np.array([1, 1, -1], np.float32) * scale
    region_faces = (weights[faces] == 1).all(axis=1)
    triangles = vertices[faces[region_faces]]
    area = float((np.linalg.norm(np.cross(triangles[:, 1]-triangles[:, 0],
                                         triangles[:, 2]-triangles[:, 0]), axis=1) * .5).sum())
    if not area > 0:
        raise ValueError("Manual mask has no valid surface triangles")
    np.savez_compressed(output / "geometry.npz", vertices=vertices, faces=faces, uv=uv,
                        vertex_colors=colors, region_weight=weights)
    k = arrays["intrinsics"].astype(float).copy()
    k[0] *= width
    k[1] *= height
    camera = {"id": "reference_camera", "width": width, "height": height,
              "intrinsics_pixels": k.tolist(),
              "world_from_camera_cv": [[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]],
              "pixel_convention": "top-left origin; pixel centers at (column+0.5,row+0.5)"}
    commit = subprocess.check_output(["git", "-C", settings["repo"], "rev-parse", "HEAD"],
                                     text=True).strip()
    report = {"status": "completed", "backend": "MoGeModel.v2.infer", "camera": camera,
              "model_id": settings.get("model_id", "Ruicheng/moge-2-vits-normal"),
              "repo": settings["repo"], "commit": commit, "weights": settings["weights"],
              "python": sys.executable, "python_version": sys.version.split()[0],
              "torch": torch.__version__, "numpy": np.__version__, "device": device,
              "checkpoint_load_warnings": load_warnings,
              "inference_seconds": inference_seconds, "total_seconds": time.monotonic()-started,
              "original_image_size": list(original.size), "inference_image_size": list(image.size),
              "valid_ratio": float(valid.mean()), "vertex_count": len(vertices), "face_count": len(faces),
              "region_face_count": int(region_faces.sum()), "region_area_m2": area,
              "bounds_xy": [float(vertices[:,0].min()), float(vertices[:,1].min()),
                            float(vertices[:,0].max()), float(vertices[:,1].max())],
              "geometry": geometry, "scale_status": "model_estimated_not_surveyed",
              "region_source": "manual_polygon", "region_polygon_normalized": polygon,
              "automatic_region_inference": False,
              "peak_gpu_mib": torch.cuda.max_memory_allocated()/2**20 if device.startswith("cuda") else None}
    (output / "moge_report.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.config, args.out)

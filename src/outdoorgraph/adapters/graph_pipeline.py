"""Run the shared generative graph in an isolated, existing Blender backend."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from outdoorgraph.adapters.contracts import resolve_uri
from outdoorgraph.adapters.pipeline import png_dimensions, stage, write_json
from outdoorgraph.ir import Scene
from outdoorgraph.validation import validate_scene


def load_graph_config(path: Path) -> dict[str, Any]:
    path = path.resolve()
    config = json.loads(path.read_text())
    for key in ("executable", "infinigen_source", "python_site_packages"):
        resource = resolve_uri(path.parent, config["blender"][key])
        if not resource.exists():
            raise FileNotFoundError(f"Missing blender.{key}: {resource}")
        config["blender"][key] = str(resource)
    if not os.access(config["blender"]["executable"], os.X_OK):
        raise ValueError("blender.executable is not executable")
    return config


def graph_description(scene: Scene, state: dict[str, Any]) -> dict[str, Any]:
    rebuilt = set(state.get("rebuilt_ids", []))
    return {"nodes": [{"id": node.id, "operator": node.operator,
                       "updated": node.id in rebuilt} for node in scene.nodes],
            "dependencies": [copy.deepcopy(edge) for edge in scene.to_dict()["dependencies"]],
            "dependency_reports": state.get("dependency_reports", [])}


def write_graph_visualization(scene: Scene, state: dict[str, Any], out: Path) -> None:
    """Small dependency visualization; colors come from actual execution records."""
    import html
    graph = graph_description(scene, state)
    write_json(out / "dependency_graph.json", graph)
    order = state.get("order", []) or [node.id for node in scene.nodes]
    order = [item for item in order if item in {node.id for node in scene.nodes}]
    order.extend(node.id for node in scene.nodes if node.id not in order)
    columns: dict[str, int] = {node_id: 0 for node_id in order}
    for _ in order:
        for edge in scene.dependencies:
            if edge.status in {"validated", "executable"}:
                columns[edge.target_id] = max(columns[edge.target_id], columns[edge.source_id] + 1)
    rows: dict[int, int] = {}
    positions = {}
    for node_id in order:
        col = min(columns[node_id], len(order))
        row = rows.get(col, 0)
        rows[col] = row + 1
        positions[node_id] = (35 + col * 245, 55 + row * 115)
    width = max(x for x, _ in positions.values()) + 235
    height = max(y for _, y in positions.values()) + 115
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#65748a"/></marker></defs>',
           '<rect width="100%" height="100%" fill="#f6f8fb"/>',
           '<text x="25" y="25" font-family="sans-serif" font-size="15">Orange: executed / Blue: cache reused / Dashed: hypothesis</text>']
    for edge in scene.dependencies:
        x, y = positions[edge.source_id]
        tx, ty = positions[edge.target_id]
        dashed = ' stroke-dasharray="5,4"' if edge.status == "proposed" else ""
        if tx-x > 245:
            lane = min(y, ty)-35
            curve = f"M{x+205},{y+15} C{x+235},{lane} {tx-25},{lane} {tx},{ty+15}"
        else:
            curve = f"M{x+205},{y+29} C{x+230},{y+29} {tx-25},{ty+29} {tx},{ty+29}"
        svg.append(f'<path d="{curve}" fill="none" stroke="#65748a"{dashed} marker-end="url(#arrow)"/>')
    rebuilt = set(state.get("rebuilt_ids", []))
    for node in scene.nodes:
        x, y = positions[node.id]
        fill = "#fff0d7" if node.id in rebuilt else "#dcecff"
        svg.append(f'<rect x="{x}" y="{y}" width="205" height="62" rx="8" fill="{fill}" stroke="#7c8b9d"/>')
        svg.append(f'<text x="{x+9}" y="{y+23}" font-family="sans-serif" font-size="13">{html.escape(node.id)}</text>')
        svg.append(f'<text x="{x+9}" y="{y+45}" font-family="monospace" font-size="11">{html.escape(node.operator)}</text>')
    svg.append('</svg>')
    (out / "dependency_graph.svg").write_text("\n".join(svg))


def run_graph(scene: Scene, config: dict[str, Any], out: Path,
              previous: Path | None = None) -> dict[str, Any]:
    """Execute arbitrary IDs through registered proxy and real Blender operators.

    ``previous`` is a prior run directory. The planner rechecks signatures and
    actual Blender collections; callers need not supply a changed-node list.
    """
    validate_scene(scene, check_cycles=False)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    previous = previous.resolve() if previous is not None else None
    previous_state = json.loads((previous / "state.json").read_text()) if previous else {}
    prior_blend = previous / "blender/scene.blend" if previous else None
    request = {"scene": scene.to_dict(), "previous_artifacts": previous_state.get("artifacts", {}),
               "previous_blend": str(prior_blend) if prior_blend and prior_blend.is_file() else None,
               "render": config.get("render", {"width": 640, "height": 480, "samples": 16})}
    write_json(out / "request.json", request)
    write_json(out / "config.resolved.json", config)
    write_json(out / "scene_ir.input.json", scene.to_dict())
    env = os.environ.copy()
    backend = config["blender"]
    source = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = os.pathsep.join([source, backend["python_site_packages"], backend["infinigen_source"]])
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0", OMP_NUM_THREADS="4", OPENBLAS_NUM_THREADS="4")
    cache = out / "cache"
    for name, folder in (("XDG_CACHE_HOME", "xdg"), ("MPLCONFIGDIR", "matplotlib"), ("BLENDER_USER_RESOURCES", "blender")):
        (cache / folder).mkdir(parents=True, exist_ok=True)
        env[name] = str(cache / folder)
    worker = Path(__file__).with_name("graph_blender_worker.py")
    command = [backend["executable"], "--background", "-noaudio", "--threads", "4",
               "--python-use-system-env", "--python-exit-code", "1", "--python", str(worker), "--"]
    records: list[dict[str, Any]] = []
    try:
        print(f"Executing shared graph in Blender: {out}", flush=True)
        stage("graph", command + ["--request", str(out / "request.json"), "--out", str(out / "blender"),
                                  "--infinigen-source", backend["infinigen_source"]],
              out, env, out, records)
        for name in ("state.json", "scene_ir.json", "validation.json"):
            (out / name).write_bytes((out / "blender" / name).read_bytes())
        state = json.loads((out / "state.json").read_text())
        migrated = Scene.from_dict(json.loads((out / "scene_ir.json").read_text()))
        write_graph_visualization(migrated, state, out)
        stage("reopen", command + ["--verify", str(out / "blender"), "--infinigen-source", backend["infinigen_source"]],
              out, env, out, records)
        validation = json.loads((out / "blender/validation.json").read_text())
        reopen = json.loads((out / "blender/reopen_verification.json").read_text())
        if not state.get("passed") or not validation.get("passed") or not reopen.get("passed"):
            raise ValueError("Graph execution or actual Blender geometry validation failed")
        width, height = png_dimensions(out / "blender/render.png")
        summary = {"status": "completed", "scene_id": scene.id,
                   "rebuilt_ids": state["rebuilt_ids"], "preserved_ids": state["preserved_ids"],
                   "render_dimensions": [width, height], "validation": validation,
                   "reopen": reopen, "execution": records}
        write_json(out / "summary.json", summary)
        return summary
    except Exception as error:
        # A failed backend may still have useful node/dependency evidence.
        for name in ("state.json", "scene_ir.json", "validation.json"):
            source_path = out / "blender" / name
            if source_path.exists():
                (out / name).write_bytes(source_path.read_bytes())
        write_json(out / "failure.json", {"status": "failed", "error": str(error), "execution": records})
        raise


def run_graph_experiment(scene_path: Path, config_path: Path, out: Path) -> dict[str, Any]:
    """Initial graph, configured edits, unchanged resume and fresh deterministic run."""
    config = load_graph_config(config_path)
    scene = Scene.from_dict(json.loads(scene_path.read_text()))
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "config.resolved.json", config)
    summaries = {"initial": run_graph(scene, config, out / "initial")}
    previous = out / "initial"
    for edit in config.get("edits", []):
        name = edit["name"]
        updated = copy.deepcopy(scene)
        updated.get_node(edit["node_id"]).params.update(copy.deepcopy(edit["params"]))
        summaries[name] = run_graph(updated, config, out / name, previous)
        previous, scene = out / name, updated
    summaries["unchanged"] = run_graph(scene, config, out / "unchanged", previous)
    summaries["fresh_repeat"] = run_graph(scene, config, out / "fresh_repeat")
    write_json(out / "summary.json", {"status": "completed", "stages": summaries})
    return {"status": "completed", "stages": summaries}

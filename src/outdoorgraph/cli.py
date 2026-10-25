"""Run deterministic, image-free Scene IR smoke tests from the command line."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from outdoorgraph.ir import Scene
from outdoorgraph.runtime import execute


def _save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(prog="outdoorgraph")
    parser.add_argument("command", choices=["run", "demo", "integrate"])
    parser.add_argument("--config", type=Path, help="External adapter configuration for integrate")
    parser.add_argument("--scene", type=Path, default=Path("configs/demo_valley.json"))
    parser.add_argument("--out", type=Path, default=Path("runs/demo_valley"))
    args = parser.parse_args()
    if args.command == "integrate":
        if args.config is None:
            parser.error("integrate requires --config")
        from outdoorgraph.adapters.pipeline import run_integration
        run_integration(args.config, args.out)
        return
    scene = Scene.from_dict(json.loads(args.scene.read_text(encoding="utf-8")))
    initial = execute(scene)
    _save(args.out / "initial.json", initial.to_dict())
    if args.command == "run":
        print(f"Initial build: passed={initial.passed}, rebuilt={initial.rebuilt_ids}")
        if not initial.passed:
            raise SystemExit(1)
        return
    updated = copy.deepcopy(scene)
    terrain = updated.get_node("terrain_main")
    terrain.params["local_bump"]["amplitude_m"] = 2.5
    edited = execute(updated, previous=initial.artifacts, changed_ids={"terrain_main"})
    _save(args.out / "edited.json", edited.to_dict())
    _save(args.out / "edited_ir.json", updated.to_dict())
    summary = {"initial_passed":initial.passed,"edited_passed":edited.passed,
               "rebuilt_after_edit": edited.rebuilt_ids,
               "preserved_after_edit":edited.preserved_ids,
               "all_checks":edited.constraint_reports}
    _save(args.out / "summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if not initial.passed or not edited.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

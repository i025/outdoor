# OutdoorGraph v0.2.1 integration validation

Validated locally on 2026-10-26, branch `feat/unified-generative-graph`, based on
stable v0.2 commit `1fedeb9ba0015e26b72cc97ee08516e34fbf62b7`. This work is submitted on the feature branch for researcher code review;
`main` remains at that commit. It is not a formal release or a main-branch merge.

## Environment and commands

Core: Python 3.11.16, pytest 9.1.1, OutdoorGraph 0.2.1 editable installation,
standard-library runtime dependencies. `pip check` reports no broken requirements.
Blender: 3.6.0, bundled Python 3.10.12; native Infinigen 1.3.3 commit
`f5bcba8de47623da9b715348cf95822445e0e9a7`, external dependencies Python 3.10.22 /
NumPy 1.26.2. Renders use CPU, four threads, 640×480, 16 samples and fixed camera.
The same core executor also imports/runs under the separate Python 3.10 backend.

Existing MoGe geometry was originally inferred with Python 3.13.15, PyTorch
2.13.0+cu126, NumPy 2.5.2 and the local MoGe v2 checkpoint. This increment did
not download weights, train a model or modify any of the six external projects.

```bash
cd ~/outdoor
conda activate ./.venv  # or activate your isolated Python >=3.11 environment
python -m pip install -e '.[dev]'
python -m pip check
python -m pytest -q

cp configs/unified_blender.json configs/unified_blender.local.json
# Replace the three /path/to backend locations in the local config.
outdoorgraph graph-demo --scene configs/unified_scene.json \
  --config configs/unified_blender.local.json --out runs/my_unified_demo

# Full suite including genuinely launched Blender/Infinigen processes:
OUTDOORGRAPH_BLENDER_CONFIG="$PWD/configs/unified_blender.local.json" \
  OUTDOORGRAPH_GRAPH_OUTPUT="$PWD/runs/my_unified_acceptance" \
  python -m pytest -q --junitxml=runs/tests-v021.xml

/path/to/moge-env/bin/python tests/external_geometry_checks.py
```

Use fresh output directories. Without the backend variable, 207 core tests pass
and nine real backend tests are skipped explicitly. With it, **216 tests passed,
zero failures/errors/skips, in 79.99 seconds**. All original 110 tests remain.
The extra external numerical suite passed **5/5** in the existing MoGe environment.

## Unified graph and initial scene

The actual tested graph contains three proxy programs (two terrain profiles and
one background) and six native Blender programs (two terrain patches, road,
grounded shelter, two independent grass fields). Every native callback runs
through the same `execute`/Planner loop as the proxy programs. The two terrain
patches share an exactly sampled continuous edge; they are separate editable
meshes rather than a welded single mesh. The scene and graph are hand authored.

IDs are configuration values. Both differently named terrain operators and
both differently named grass operators execute through the same type registry.
Native grass geometry and distribution call existing `GrassTuftFactory`,
`make_asset_collection`, `scatter_instances`; OutdoorGraph supplies selection
faces and road exclusion. No new vegetation generator was written.

## Measured editing results

Final run: `runs/unified_v021_acceptance_final/`.

| Stage | Rebuilt | Preserved | Near/far actual grass instances | Actual road samples | Max road contact error |
|---|---:|---:|---|---:|---:|
| Initial | 9 | 0 | 39 / 49 | 3870 | 0.015007629 m |
| A: local compact height bump +1.2 m | 5 | 4 | 40 / 49 | 3870 | 0.020846868 m |
| B: road width 1.1 → 2.8 m | 2 | 7 | 36 / 49 | 9169 | 0.022245478 m |
| Unchanged resume | 0 | 9 | 36 / 49 | 9169 | 0.022245478 m |
| Fresh deterministic repeat of B | 9 | 0 | 36 / 49 | 9169 | 0.022245478 m |
| Add road → far grass dependency | 1 | 8 | 36 / 49 | 9169 | 0.022245478 m |
| BLEND cache missing | 6 | 3 | 36 / 49 | 9169 | 0.022245478 m |
| Remove far grass program/resources | 0 | 8 | 36 / removed | 9169 | 0.022245478 m |

The shared actual terrain edge has zero measured gap at 129 samples, limit
0.0001 m. The shelter's actual bottom-center contact error after A/B is
2.98e-7 m. Grass instance-origin contact errors are below 1.91e-6 m. After B,
near-grass centerline clearance is **2.217455 m**, above required half-width plus
buffer **2.0 m**. The far region has 49 identical instances across A/B, with
unchanged stable IDs, transforms, geometry and custom properties.

Roads have 85 generated centerline samples from four control points. Real mesh
validation covers vertices, triangle interiors and edge midpoints, not just
those control points. The constant contact limit is 0.025 m; the road explicitly
sits 0.015 m above the surface for rendering. Meshing/interpolation account for
the remaining finite deviation. No acceptance threshold was relaxed.

For A, rebuilt IDs are `upland_profile`, `west_ground`, `access_track`,
`wayside_shelter`, `meadow`; the eastern terrain, far grass, eastern profile and
background are preserved. B rebuilds only `access_track` and `meadow`. The
dependency edit rebuilds only `remote_meadow`; all other eight nodes retain
their results, and real preserved backend snapshots compare exactly.

Missing the saved BLEND rebuilds all six native resources while retaining three
valid proxy artifacts. Deleting the far program deletes its actual private
collection, source assets and scatter without changing preserved nodes.
Incremental B and a fresh fixed-seed B have identical instance transforms,
stable object IDs, geometry and generation properties. Every successful stage
opens in a separate Blender process, decodes RGB and imports its GLB.

## Rejected real failures

Two deliberate negatives are passing regression tests because they must fail
execution:

1. Raise the road by 0.4 m. Its actual 9169 mesh samples measure approximately
   **0.403309 m > 0.025 m**. The postcondition rejects it immediately, downstream
   grass is blocked and no successful scene summary is produced. The named
   scene contact constraint is recorded as blocked because the rejected road
   is not an admissible artifact; numeric evidence is in the failed postcheck.
2. Give two otherwise identical nonlinear terrain profiles different mesh
   resolutions (0.25 versus 2 m). The analytic seam check passes, but actual
   mesh interpolation measures **0.045546405 m > 0.0001 m** at 129 samples.
   Final state becomes failed, both native artifacts lose successful-cache
   status, and related dependencies are not executable. The numeric evidence
   is retained in `failures[].check_evidence` and the actual constraint report.

Unit regressions also cover port/kind/missing-node rejection, unknown semantic
hypotheses, valid proposal promotion, typed cycles, false conditions, parameter/
seed/version/provenance edits, adding/removing/rewiring dependencies, ordered
multiple inputs, partial caches and late-ready hard-constraint failure
propagation. A late failure invalidates already-generated descendants too.

## Existing MoGe workflow compatibility

The old `--bundle` Blender entry now delegates to the same Planner. Using the
existing actual MoGe v2 NPZ/bundle, the updated path generated **86 native grass
instances**, two real 640×318 CPU renders, BLEND and GLB. The independent legacy
verifier reopened the scene, checked calibration/provenance/mask/contact and
imported GLB with **2 mesh objects / 90255 faces**. This compatibility experiment
reused already-inferred geometry; it did not rerun or simulate model inference.
The old `run`, `demo`, `integrate` commands and Scene schema remain supported.
Optional missing dependency ports are normalized when checking older outputs.

## Saved evidence and review

Every stage has `state.json`, `scene_ir.json`, `request.json`, configuration,
`execution.json`, backend logs and `blender/validation.json`. Successful stages
also have `dependency_graph.{json,svg}`, `blender/scene.blend`, `scene.glb`,
`render.png`, `objects.json` and `reopen_verification.json`. The snapshot records
contain actual mesh coordinates, instance transforms and metadata; no extra
geometry/file hash-verification scheme was added. Planner signatures implement
the requested cache identity only.

Small RGB panels, metrics and the graph are in
[examples/v021](../examples/v021/README.md). Large run products,
environments, checkpoints, caches and local backend paths remain ignored.
The main source changes are IR/validation/runtime, unified `execution.py`,
existing geometry operators/constraints, native graph worker, launcher/CLI,
regression tests, portable configs and method/API documentation.

## Limits

The graph, regions and edits are authored. Agentic program induction and joint
global optimization are not implemented. Terrain continuity/contact are finite
mesh checks, not analytic proofs of all surface points. Grass checks concern
instance centers/origins, not all blades or collision-free physical support.
The independent object is a simple shelter proxy, and the terrain uses the
existing lightweight heightfield rather than full Infinigen terrain generation.
The legacy MoGe output is still a single-view 2.5D surface with estimated scale.
Backend installs and weights must already exist. An external adapter that
replaces an asset outside the graph must update its declared input identity;
arbitrary in-place external-file changes are not detected with file hashing.

Changes are prepared on the feature branch for code review. No merge into
`main` or formal release is part of this work; no third-party repository was modified.

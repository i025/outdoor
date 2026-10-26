# OutdoorGraph v0.2.1 — Unified Executable Generative Graph

**Research direction:** Agentic Inverse Procedural Outdoor Reconstruction with
Executable Generative Dependencies.

OutdoorGraph studies editable procedural programs, execution dependencies and
spatial constraints above independently reproduced systems. Third-party code,
weights and environments remain separate.

**Implemented:** one execution planner for proxy, Blender and external adapter operators,
typed dependency validation, automatic incremental caching, sampled spatial/topology
constraints, and real **MoGe v2 → SceneBundle → Infinigen GrassTuft/Scatter → Blender** integration.
**Not implemented:** automatic mechanism induction, image-inferred causality,
semantic masks/gravity, unseen-surface completion or global optimization.

## Actual small example

![Actual Blender reference-view render](examples/mountain_v02/render.png)

This is a real CPU Blender render of a **single-view 2.5D textured surface** with
missing sky/discontinuity holes and native procedural grass. It is not a complete
reconstructed world. The [example](examples/mountain_v02/README.md) includes both
renders, manual mask, portable config, provenance and measured validation summary.

The run generated **86 grass instances**; 86/86 instance centers projected into
the manual mask. Maximum instance-origin surface contact error was **8.31394e-6 m**.
These checks do not certify blade containment, collision freedom or ground-truth
reconstruction accuracy. Large scenes, raw arrays, weights and datasets are
excluded from Git; the input remains in the recorded upstream MoGe example set.

## Unified execution and real incremental editing

![Actual initial and edited Blender renders](examples/v021/comparison.png)

[Measured editing example](examples/v021/README.md) retains the original editing
results. The [P0 revalidation](docs/p0_review_validation_v0.2.1.md) passes 253 tests,
including 12 actual Blender regressions, plus five external geometry checks.


The same `Planner` schedules registered proxy and real Blender callbacks from the
Scene IR. Operators declare input slots, output kinds, parameters, version, seed,
pre/postconditions, backend and artifact metadata. IDs are scene data, not executor
branches. Old `run`, `demo` and `integrate` commands remain supported.

New dependencies default to `proposed`; legacy `validated` edges are rechecked
through an extensible typed RelationSpec registry. Proposed relations need explicit
validation; confidence alone grants no permission. Execution records preserve the
contract gate, actual execution results and related hard-constraint evidence.
Unknown semantic hypotheses remain proposed. A failed hard constraint fails the
run and blocks affected downstream execution.

Cache signatures cover operator/version/backend, parameters, bounds, seed,
provenance metadata, upstream results/ports/relations and the contents of declared
external files. Same-path image/mesh/texture changes invalidate their consuming
subgraph; unchanged inputs keep cache hits. Parameter and graph
edits are detected without a caller dirty list. Missing Blender objects cause
regeneration. Preserved node collections, native instances and metadata are
compared directly after reopening the previous scene. Physical dependencies require
validation evidence matching their exact source and target. See the
[P0 review validation](docs/p0_review_validation_v0.2.1.md) for regression results
and explicit migration of older dependencies with no status.

```bash
cp configs/unified_blender.json configs/unified_blender.local.json
# Fill in the existing Blender, Infinigen source and Python site-packages paths.
outdoorgraph graph-demo \
  --scene configs/unified_scene.json \
  --config configs/unified_blender.local.json \
  --out runs/unified_v021

# Execute any supported graph, optionally resuming a prior run:
outdoorgraph graph --scene configs/unified_scene.json \
  --config configs/unified_blender.local.json --out runs/graph_initial
outdoorgraph graph --scene /path/to/edited_scene.json \
  --config configs/unified_blender.local.json \
  --previous runs/graph_initial --out runs/graph_edited
```

The experiment uses two adjacent continuous terrain patches, two independent
native Infinigen grass regions, a terrain-conforming road and a grounded object.
Configured edits raise local terrain, then widen the road. The driver also runs
an unchanged resume and a fresh fixed-seed repeat. Each stage saves BLEND/GLB, RGB,
IR, state, dependency visualization, geometry checks, commands and logs. Roads
are checked between control points and against actual Blender mesh samples.

```bash
# Opt-in suite actually launches Blender/Infinigen; no model download:
OUTDOORGRAPH_BLENDER_CONFIG="$PWD/configs/unified_blender.local.json" \
  python -m pytest -q tests/test_blender_graph.py
```

See [execution/operator API](docs/execution_api.md),
[method v0.2.1](docs/method_design_v0.2.1.md), and the
[real editing report](docs/integration_validation_v0.2.1.md).

## Core quick start

Requires **Python ≥3.11**; tested with **3.11.16**. Core runtime dependencies are
standard library only; `dev` installs pytest. Do not replace system Python 3.8.

```bash
git clone https://github.com/i025/outdoor.git
cd outdoor
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pip check
python -m pytest -q
outdoorgraph demo --scene configs/demo_valley.json --out runs/demo_valley
```

The default suite has **241 passing core tests**, retaining the prior suite;
12 real-backend tests are explicitly skipped until backend configuration is supplied.
With `OUTDOORGRAPH_BLENDER_CONFIG`, **253 tests pass with no skips**. See
[environment setup](docs/environment.md) for Conda and tested package versions.
`environment.yml` creates an isolated core environment; heavy backend dependencies
are not installed there.

The proxy demo is hand-authored synthetic geometry: five nodes, four rebuilt
dependents after a terrain edit, and preserved background. It writes
`runs/demo_valley/{initial,edited,edited_ir,summary}.json`. Hard failures return
nonzero after reports are saved. Road contact is checked at finite dense samples along the generated polyline,
including segment interiors; this is not a continuous mathematical guarantee.

## Real MoGe/Infinigen integration

First prepare independent MoGe v2 and Infinigen/Blender installations and a local
`Ruicheng/moge-2-vits-normal` checkpoint. They are not bundled or automatically
downloaded. The verified stacks were:

| Component | Tested environment |
|---|---|
| OutdoorGraph 0.2.1 | Python 3.11.16; pytest 9.1.1 |
| MoGe v2 | Python 3.13.15; torch 2.13.0+cu126; numpy 2.5.2; A100 |
| Infinigen 1.3.3 | Blender 3.6.0/Python 3.10.12; numpy 1.26.2; CPU render |

MoGe's source minimum is Python 3.10; 3.13 is the tested environment. Infinigen's
tested release requires its separate Python 3.10/Blender 3.6 stack. Follow
[backend setup](docs/environment.md), choosing PyTorch compatible with the GPU driver.

```bash
cp configs/integration_mountain.json configs/integration_mountain.local.json
# Edit *.local.json: replace /path/to/... with actual interpreter/repo/weight paths.
# Supports ~ and config-relative paths; $ENV_VAR is NOT expanded.
outdoorgraph integrate \
  --config configs/integration_mountain.local.json \
  --out runs/my_mountain_v02
```

Use a new output directory; existing runs are not overwritten. The portable
config retains the verified seed, polygon and generation settings. Actual counts
may vary across backend versions. Additional numerical tests run externally:

```bash
/path/to/moge-env/bin/python tests/external_geometry_checks.py
```

The worker calls the official MoGe API and writes NPZ/PNG because the tested
OpenCV build cannot export EXR through its official CLI. Infinigen provides the
grass geometry, materials and scatter algorithms. Each run records commands,
statuses, timings and logs. A fresh Blender process reopens the scene, checks
bundle/provenance/IDs/calibration/native instances/contact/mask, decodes the
renders, then independently imports GLB and inspects geometry and extras.
Failures return nonzero and preserve `failure.json`; mocks are not execution evidence.

## Representation and documentation

Core Scene schema **0.1** remains compatible; optional dependency ports and the
`executable` lifecycle status preserve old files without a new schema version. SceneBundle **0.2** adds meters,
right-handed Z-up coordinates, CV camera calibration, asset/mask references,
generator settings, seeds and provenance. Proposed hypotheses are not executed
without validation. BLEND embeds the bundle and textures; GLB keeps geometry and
provenance extras. Instance IDs are snapshots for identical config and backend
versions, not cross-edit correspondences. Fixed worker entry points and subprocess
isolation do not constitute a security sandbox.

| Document | Contents |
|---|---|
| [Method design v0.1](docs/method_design_v0.1.md) | Original representation and roadmap |
| [Method design v0.2](docs/method_design_v0.2.md) | Existing-system integration boundary |
| [Method design v0.2.1](docs/method_design_v0.2.1.md) | Unified executable graph and incremental editing |
| [Execution/operator API](docs/execution_api.md) | Contracts, dependency lifecycle, cache and induction-facing API |
| [Adapter interface](docs/adapter_interface.md) | Coordinates, cameras, files, IDs and processes |
| [Six-system inventory](docs/integration_inventory.md) | Inspected revisions, environments, interfaces and licenses |
| [Validation report](docs/integration_validation_v0.2.md) | Actual execution, measurements and limitations |
| [Environment setup](docs/environment.md) | Core and independent backend reproduction |

UrbanWorld2.0/fal-worldclaw have independent integration plans. RCWM remains an
Agentic baseline; Building Rome is an optional geometry prior/separate baseline.
They are not merged into OutdoorGraph. The inventory distinguishes historical
success from current migration failures.

## Repository layout

```text
configs/                   Portable scene and integration config
docs/                      Method, interface, inventory and validation
examples/mountain_v02/     Small real renders, mask, config and provenance
src/outdoorgraph/          IR, validation, operators, runtime and CLI
src/outdoorgraph/adapters/ External workers and interchange contract
tests/                     Core graph tests, external geometry and opt-in Blender checks
benchmarks/ experiments/   Protocols; no external data or weights
```

## Research and licensing

Record camera conventions, seeds, model versions, configs and tool traces.
Separate ground truth from hypotheses. Reference texture similarity is not
independent reconstruction evidence, and a novel render is not held-out truth.

No root source license has been assigned. Upstream models/data/assets retain
their own terms: see the inventory for BSD/MIT/GPL components, Building Rome's
noncommercial contributions and fal-worldclaw's missing project-level license.

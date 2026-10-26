# v0.2.1 P0 review fixes and revalidation

Validated on 2026-10-26 on `feat/unified-generative-graph`, against review baseline
`a0ba3ea8d5c41408527b000609eed313a1247120`. This is a feature-branch review increment;
main remains at `1fedeb9ba0015e26b72cc97ee08516e34fbf62b7`.

## Fixes

1. **External content cache inputs.** `OperatorSpec.external_inputs` declares a
   per-node mapping or resolver for local files. The executor streams their
   SHA-256 content identities before lookup and records them in artifact
   metadata. Same path/size/mtime with different bytes invalidates this producer
   and its consumers; touching unchanged bytes retains cache hits. Missing inputs
   fail before reuse. Files changed during execution cannot publish a stale
   result. The MoGe mesh adapter declares its actual NPZ and reference texture.
   Rebuilt Blender surfaces load private new packed images, fixing Blender's
   same-path image-cache reuse without changing another surface's pixels.
2. **Typed semantic relations.** Extensible `RelationSpec` / `RelationRegistry`
   declarations bind allowed endpoint kinds/operators, ports, mandatory semantic
   validators and, for physical claims, mandatory geometry validators. A slot
   allowlist is not registration. Builtins distinguish analytic heightfields
   from native/reconstructed meshes, so type-compatible but unusable programs
   cannot silently sample an empty/default heightfield. Physical evidence must
   match the exact source/target, rather than borrow an unrelated passed check.
3. **Explicit lifecycle.** New and missing-status dependencies are proposed.
   `Planner.validate_dependencies()` explicitly admits legal proposals and saves
   semantic evidence and the original declared status. Execution reruns the
   contract and relation geometry, including cache hits. Required physical
   checks run before a downstream callback consumes the target; failure blocks
   descendants and invalidates their cache. Only successful required evidence
   permits executable status. Evidence is in `dependency_reports`, status in IR.

Planner and native Executor remain incremental implementations. Existing
Infinigen terrain/vegetation systems were not reimplemented, and none of the six
external repositories were modified. No inference weights were downloaded or
models trained/run for this increment.

## Tests and reproduction

Environment: OutdoorGraph 0.2.1 editable install, Python 3.11.16, pytest 9.1.1,
stdlib runtime dependencies; Blender 3.6.0 / Python 3.10.12, native Infinigen 1.3.3
commit `f5bcba8de47623da9b715348cf95822445e0e9a7`, NumPy 1.26.2, CPU rendering.
External numerical tests use the existing Python 3.13.15 / NumPy 2.5.2 environment.

**253 tests pass: 241 core + 12 real Blender tests, no skips or failures**
(final source run: 122.84 seconds).
The separate external numerical suite passes **5/5**. This retains the prior
216-test suite and adds 37 tests (34 core, three real backend tests).

| New regression coverage | Tests |
|---|---:|
| External bytes/mtime/missing/base-path/separate branches/mutation | 11 |
| Typed relation registration, validators, ports and representations | 9 |
| Default proposal, explicit legacy migration, permission and evidence | 5 |
| Wrong support evidence, rejected edge, symlink target, immediate blocking | 4 |
| Unrelated edge insertion preserves cached metadata | 1 |
| Optional soft/hard constraint CLI exits | 4 |
| Real same-path packed texture, NPZ mesh/native grass, missing file | 3 |

Each P0 was reproduced with failing tests before implementation. Logs preserve
red/green comparisons for the baseline same-path stale cache, lifecycle/semantic
failures, incorrect support evidence, missing immediate execution gate,
representation mismatch and status-source reporting. The original floating-road
CLI case remains and is stricter: making a duplicate Scene constraint soft
cannot disable mandatory physical dependency validation. Separate optional
population constraints still cover the original soft/hard CLI behavior.

```bash
cd ~/outdoor
# Activate the existing isolated Python >=3.11 environment.
python -m pip check
python -m pytest -q                       # 241 pass, 12 explicit backend skips

cp configs/unified_blender.json configs/unified_blender.local.json
# Fill the existing Blender, Infinigen source and site-packages paths.
# All output directories below must be new.
OUTDOORGRAPH_BLENDER_CONFIG="$PWD/configs/unified_blender.local.json" \
OUTDOORGRAPH_GRAPH_OUTPUT="$PWD/runs/unified_v021_p0_final" \
OUTDOORGRAPH_EXTERNAL_OUTPUT="$PWD/runs/external_v021_p0_final" \
  python -m pytest -q --junitxml=runs/p0-tests.xml

/path/to/moge-env/bin/python tests/external_geometry_checks.py
```

The fixture generator in `tests/test_blender_external_inputs.py` authors tiny
MoGe-format NPZ planes and solid PNGs using Blender's installed NumPy/bpy. It is
explicitly a loader/cache regression fixture, **not MoGe model inference**. Its
geometry, native Infinigen scatter, rendering, packed-image audit and independent
BLEND/GLB reopen are real subprocess executions.

## Real editing results

Final mixed-scene outputs: `runs/unified_v021_p0_final/`. Each successful stage
has Scene IR, execution/dependency reports, graph JSON/SVG, actual object snapshots,
RGB, BLEND, GLB and independent reopen checks. Roads are checked over dense mesh
vertices, edge midpoints and face interiors, beyond their four control points.

| Stage | Rebuilt / preserved | Near / far native instances | Road samples | Max contact error |
|---|---|---|---:|---:|
| Initial | 9 / 0 | 39 / 49 | 3870 | 0.015007629 m |
| A: local terrain +1.2 m | 5 / 4 | 40 / 49 | 3870 | 0.020846868 m |
| B: road width 1.1 to 2.8 m | 2 / 7 | 36 / 49 | 9169 | 0.022245478 m |
| Unchanged resume | 0 / 9 | 36 / 49 | 9169 | 0.022245478 m |
| Fresh fixed-seed repeat | 9 / 0 | 36 / 49 | 9169 | 0.022245478 m |
| Add road to far-grass dependency | 1 / 8 | 36 / 49 | 9169 | 0.022245478 m |
| Missing BLEND cache | 6 / 3 | 36 / 49 | 9169 | 0.022245478 m |
| Remove far program/resources | 0 / 8 | 36 / removed | 9169 | 0.022245478 m |

The road limit stays 0.025 m. Preserved collections, geometry, instances and
metadata compare exactly; fixed-seed fresh and incremental results agree.
Deliberate floating-road failure measures 0.403309286 m > 0.025 m and blocks
near grass. Unequal terrain mesh resolutions measure a 0.045546405 m boundary
gap > 0.0001 m at 129 samples; the final actual-mesh gate rejects it and revokes
successful artifact/dependency status. Both negative regressions pass by proving
the execution fails and retains numeric evidence, without relaxed thresholds.

## External files and existing MoGe compatibility

Final cache experiment: `runs/external_v021_p0_final/`:

- Unchanged: all four nodes reused.
- Same reference-image path changes red to blue: only `surface_main` and
  `native_plants` rebuild; actual packed pixels change [1,0,0,1] to [0,0,1,1].
  Independent `surface_other` keeps identical pixels, geometry and properties.
- Same NPZ path changes height: only that surface and its grass rebuild; actual
  mesh vertices and native instance origins move from z=0 to z=0.5 m.
- Missing NPZ: producer fails before cache use, native consumer is blocked,
  independent surface/background remain preserved; no successful scene is saved.

[Small actual renders and machine summary](../examples/v021/p0_cache/README.md)
are checked into examples; generated BLEND/GLB/NPZ remain ignored.

The existing, previously inferred MoGe mountain NPZ was also consumed through the
legacy `--bundle` bridge after setting its dependency to proposed. The adapter
records explicit semantic admission then actual endpoint geometry, reaching
executable status. It produced 86 native instances, two 640x318 CPU renders,
BLEND and GLB; independent legacy verification passed camera/mask/contact and
GLB import (90255 faces). Maximum origin contact error: 8.31394e-6 m;
86/86 centers project into the manual mask. Cached geometry was reused;
no new MoGe inference is claimed.

## Submitted files

29 files include the incremental implementation, retained/migrated tests, new
regressions, API/method/revalidation documentation and small actual renders:

```text
README.md
docs/execution_api.md
docs/integration_validation_v0.2.1.md
docs/method_design_v0.1.md
docs/method_design_v0.2.1.md
docs/p0_review_validation_v0.2.1.md
examples/v021/p0_cache/README.md
examples/v021/p0_cache/baseline.png
examples/v021/p0_cache/geometry.png
examples/v021/p0_cache/results.json
examples/v021/p0_cache/texture.png
src/outdoorgraph/adapters/blender_worker.py
src/outdoorgraph/adapters/graph_blender_worker.py
src/outdoorgraph/adapters/pipeline.py
src/outdoorgraph/execution.py
src/outdoorgraph/external_inputs.py
src/outdoorgraph/ir.py
src/outdoorgraph/relations.py
tests/test_adapters.py
tests/test_blender_external_inputs.py
tests/test_blender_graph.py
tests/test_cli.py
tests/test_dependency_lifecycle.py
tests/test_execution.py
tests/test_external_inputs.py
tests/test_ir.py
tests/test_p0_review.py
tests/test_pipeline.py
tests/test_relations.py
```

## Compatibility and limits

Existing explicit validated/executable Scene IR remains loadable and is fully
rechecked. Old JSON without status now loads as proposed: callers must explicitly
validate with the relevant registry and save its report/returned scene. Unknown
or invalid legacy semantics cannot be silently made valid. New custom relations
require trusted registration and validation, rather than only an InputSlot name.
An invalid physical dependency now fails even if a duplicate Scene check is soft.

External adapters must declare every file they consume; hidden inputs or remote
mutable services are not automatically discovered. Actual grass region weights
are baked into NPZ `region_weight`; changing the audit mask PNG alone does not
recompute them. Rebuild the geometry/weights through the existing pipeline when
editing that mask. Hashing costs I/O proportional to declared input bytes.

Scene authorship/segmentation and induction remain manual; finite origin/contact
checks do not establish full-blade collision freedom or physical support. Root
source licensing remains unspecified, while upstream licenses stay independent.
No main merge, release, model integration or v0.3 development is included.

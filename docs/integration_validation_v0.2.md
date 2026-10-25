# OutdoorGraph v0.2 integration validation

Validation date: **2026-10-25**. These measurements come from an actual local
MoGe/Infinigen/Blender run, separate from unit-test fixtures. The committed
[example](../examples/mountain_v02/README.md) is a compact extract of that run;
its JSON records source-report and image hashes. Large generated scenes, model
weights and raw arrays are retained locally under `runs/mountain_v02_verified/`
and are deliberately not committed.

## Execution and environments

| Stage | Actual implementation/environment | Result |
|---|---|---|
| Core orchestration | OutdoorGraph 0.2.0, Python 3.11.16 | File contracts, process scheduling and validation |
| Geometry inference | Official MoGe v2 API, Python 3.13.15, torch 2.13.0+cu126, A100 | Completed real inference with local `moge-2-vits-normal` weights |
| Natural generation/render | Infinigen 1.3.3, Blender 3.6.0/Python 3.10.12, CPU/4 threads/16 samples | Native grass assets, native Scatter, BLEND/GLB and two PNG renders |
| Independent verification | A second Blender process | Reopened BLEND, evaluated instances, decoded PNG, imported GLB |

The original official MoGe CLI performed inference but failed to export EXR in
the tested OpenCV 5 build. The adapter therefore calls the same public
`MoGeModel.from_pretrained(...).infer(...)` API and serializes NPZ/PNG. It does
not reimplement reconstruction. The existing `GrassTuftFactory`,
`make_asset_collection` and `scatter_instances` supply vegetation geometry,
materials, distribution and instancing; their implementation is not copied here.

| Source | Inspected Git revision |
|---|---|
| MoGe | `74fbce054ebed49800de42d0ad0e83495065719a` |
| Infinigen | `f5bcba8de47623da9b715348cf95822445e0e9a7` |

The input is MoGe's `example_images/05_Mountain.jpg`, 2048×1015, resized without
cropping to 512×254. Seed is 2026. The manual normalized polygon, model ID,
input hash, dependency versions and portable run configuration are included in
the example. No external API key or paid service was used for this integration.

## Measured results

| Check | Actual result |
|---|---|
| Reconstructed interchange mesh | 5,464 vertices; 9,741 triangle faces |
| Manual selection | 438 faces; model-estimated area 41.9061355591 m² |
| Native grass assets / instances | 2 source assets; target expectation 80; actual instances **86** |
| Instance-center mask projection | **86 inside / 0 outside** |
| Maximum instance-origin surface distance | **8.31394027045e-6 m**, tolerance 0.001 m |
| Maximum calibrated camera projection error | **2.97055485109e-5 px**, tolerance 0.001 px |
| Reopened BLEND | Full embedded bundle/provenance matches; 7 unique tagged object IDs; 1 packed image |
| Independently imported GLB | 2 mesh objects; 90,255 faces; node extras/provenance preserved |
| Real decoded renders | `render.png` and `overview.png`, each 640×318, finite nonconstant pixels |

Actual external process times were approximately 5.325 s for MoGe,
9.886 s for generation/render/export and 1.318 s for independent verification.
All three returned exit code 0. These are measurements of one local run, not
portable performance guarantees. Files and exact commands remain in the local
run's `execution.json`, logs, IR/bundle and `blender/reopen_verification.json`.

## Tests and publication checks

- **110 core pytest tests pass**, including the original 37 tests, 45 adapter
  contract tests and 28 process/output-validation tests.
- **5 external NumPy numerical tests pass**, covering triangle orientation/UV,
  conservative validity during decimation, depth discontinuities, unused-vertex
  compaction and region/color index alignment.
- Core tests do not require model weights, GPU, NumPy or Blender. Numerical tests
  run explicitly with a NumPy-capable external Python.
- Real subprocess failure/timeout tests and deliberately invalid header fixtures
  validate rejection behavior; they are not used as evidence of model or renderer
  success. BLEND/GLB/image acceptance uses actual independent reopen/import/decode.
- Publication contains first-party source, tests, configs, method/interface
  documents and a small real example. Environment/cache/run/weight/scene files and
  local credentials are ignored; staged files are checked before commit.

See [environment setup](environment.md) for commands and version boundaries.

## Completed, partial and unimplemented

**Completed:** inventory of six existing systems; a common file/process
contract; actual MoGe v2 and native Infinigen/Blender execution; scene/render
files; persistent IDs/provenance/configuration; independent artifact verification;
and passing core/numerical tests.

**Partial:** the surface is single-view 2.5D with reference-image texture,
estimated scale, explicit manual mask and declared world orientation. Sky and
depth discontinuities can leave holes; novel views expose texture stretching.
The present overview is a local selected-region render, not full 3D recovery.

**Unimplemented:** automatic semantic masks/gravity, generative mechanism
induction, image-inferred causal dependencies, invisible geometry completion,
multiview reconstruction and global consistency optimization. UrbanWorld2.0 and
fal-worldclaw have interface plans only; RCWM and Building Rome remain independent
baselines. Existing third-party migration/installation issues are documented,
not repaired by modifying their original repositories.

The contact check measures **instance origins**, and the mask check measures
**instance centers**. They do not certify whole-blade containment, collision
freedom, scene physics or surveyed metric accuracy. Reference-texture similarity
is not independent reconstruction accuracy; a new render is not held-out ground
truth. Identical seeds provide snapshots only with matching backend versions and
configuration, not guaranteed correspondences after edits.

The [inventory](integration_inventory.md) records third-party local changes and
license/dependency restrictions; no third-party repositories or weights are
merged into this project.

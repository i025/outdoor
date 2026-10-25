# OutdoorGraph v0.2 method boundary

v0.2 establishes an execution layer for research above existing systems. It
does not replace MoGe's model or Infinigen's procedural vegetation algorithms.
The original [v0.1 design](method_design_v0.1.md) remains the foundation.

```mermaid
flowchart LR
  I[Reference image] --> M[Existing MoGe v2]
  M --> G[Points/depth/mask/K]
  G --> B[Scene IR and SceneBundle]
  H[Manual region and world orientation] --> B
  B --> P[Existing Infinigen grass and scatter]
  P --> R[Blender scene and renders]
  R --> V[Independent reopen and GLB import]
```

Core Scene 0.1 separates programs, execution dependencies, constraints and
evidence. Bundle 0.2 adds calibration, resource references, coordinates and
provenance. Independent Python/Blender processes keep incompatible dependencies
separate. Model point maps are converted to metric Z-up using a declared rigid
rotation; scale remains model-estimated and gravity/world orientation is manual.
Discontinuity-aware conversion does not reconstruct unseen surfaces.

The graph declares the surface as grass support. This is an adapter contract,
not a causal dependency induced from the image. Manual semantic selection is
distinct from geometric validity. Generator/revision/seed/parameter/ID metadata
survives scene assembly and export.

Real execution is checked separately from unit tests: a fresh Blender process
reopens the scene, compares embedded data, evaluates native instances, checks
camera projection, instance-origin surface distance and center-mask projection,
decodes images, and independently imports GLB. Any failed check fails the run.
Mock responses and file headers alone do not establish integration success.

These checks establish a data/execution path and limited geometric consistency.
They do not establish physical correctness, blade containment, collision freedom,
metric accuracy or reconstruction quality. Texture reuse, single-view holes and
novel-view stretching remain explicit limitations.

Remaining work: infer regions/gravity/scale with uncertainty; propose alternative
generative mechanisms; validate dependencies through controlled edits; jointly
optimize camera/geometry/generator parameters while preserving unrelated areas;
evaluate independent views and separate RCWM/Building Rome baselines.

See [interface](adapter_interface.md), [inventory](integration_inventory.md) and
[actual validation](integration_validation_v0.2.md) for the implementation scope.

# First implementation issues

## Issue 1 — Camera-aware image evidence adapter

**Goal:** Convert a single reference image and MoGe outputs (depth, normals, camera prior) into a normalized local metric scene frame, storing observation provenance and uncertainty. Do not invent metric scale for single-view results; require an anchor or represent scale ambiguity.

**Acceptance:** round-trip known synthetic camera, image/geometry IDs, consistent z-up conversion, uncertainty marked, at least 3 independent tests.

## Issue 2 — Blender proxy renderer

**Goal:** Execute IR on Blender with terrain, road, vegetation proxies and building; render both initial and edited states from a fixed calibrated camera and one held-out camera.

**Acceptance:** reproducible render artifacts with object IDs; no unexpected object mutation; frame and unit test; explain unresolved Blender version prerequisites.

## Issue 3 — Constraint compilation + relation alternatives

**Goal:** Distinguish inferred relationships from validated runtime dependencies; compile proposed relations to typed primitive validators and allow alternatives where observation is insufficient.

**Acceptance:** 2 valid inferred relation samples, 2 rejected unsafe proposals, confidence and provenance retained, simulated edit tested with and without compiled edges.

## Issue 4 — Nontrivial repair solver

**Goal:** Add a solver that minimizes spatial damage to unaffected areas while resolving violated ground contact, road path and cross-region seam constraints. Do not assume all constraints can be satisfied simultaneously.

**Acceptance:** constrained edit succeeds in positive cases, impossible case returns explicit failure, unrelated unchanged artifacts are preserved.

## Issue 5 — Multi-domain structured GT evaluation

**Goal:** Specify and build scenes covering forested valley + road, rural farm + field rows, and peri-urban street + river; capture reference and held-out cameras, editable object/terrain GT and local edit scripts.

**Acceptance:** reproducible scene source, camera metadata, separate observed/hidden metrics, edit constraint benchmark and at least one meaningful ablation.

> Historical v0.1 design. The executable substrate now uses the [v0.2.1 unified planner](method_design_v0.2.1.md) and [operator API](execution_api.md). Agentic program induction remains research work, not an implemented capability.

# OutdoorGraph — Method Design v0.1

**Working title:** *Agentic Inverse Procedural Outdoor Reconstruction via Executable Generative Dependencies*

**Status:** Research hypothesis and testable first-stage specification — **not** a completed method or publication claim.

## 1. Problem and target

**Inputs.** One or more photos or concept-art images of an outdoor environment, optionally with camera poses, depth, OSM/DEM or other hints. Extra inputs must remain optional; evaluate image-only performance separately.

**Outputs.** An editable 3D outdoor scene composed of continuous terrain and other fields, curves/topological features (roads, rivers), distributed populations (vegetation, rocks, crops), independent objects (buildings, landmarks), and a *re-executable dependency graph* that controls generation and edits. The scene should render faithfully from observed and held-out views and should remain spatially consistent after supported edits.

**Spatial scope.** Quantitative research starts at ~100–300 m mixed scenes. 500 m–2 km is a scalability target, not a v0.1 delivery commitment. Scenes should cover natural landscapes, countryside/farms, and urban-edge/city mixes, not only forests.

**Research question.** Can inferring a graph of scene *generating processes* and *executable spatial dependencies* from image evidence improve both initial geometric/visual reconstruction and dependency-preserving local edits compared with static geometric reconstruction, fixed procedural templates, and unconstrained code-generating agents?

## 2. Proposed contributions — to be validated experimentally

1. **Image-conditioned generative mechanism induction.** Decompose outdoor scenes by *generative behavior* (continuous fields, stochastic populations, structured layouts, salient object instances), infer suitable procedural structure and parameters, and permit constrained structural edits of the program itself. A fixed operator-only solution is a baseline, not enough by itself.
2. **Executable generative dependency representation.** Represent dependencies of generation programs and explicit verifiable constraints, with provenance and uncertainty. Distinguish **runtime dependency** (if terrain changes, road must recompute) from **spatial relation** (road must remain connected). Do not infer physical causality solely from co-occurrence in RGB pixels.
3. **Joint reconstruction and localized edit consistency.** Use the same dependency representation for global reconstruction refinement and for selective re-evaluation after edits. Preserve unaffected nodes, enforce geometry/topology constraints, and flag unresolvable edits instead of claiming success.

## 3. Scene representation

- **Node:** stable ID, semantic kind, generator operator, typed/physical parameters, bounds, deterministic seed, evidence references, optional hypothesis metadata.
- **Dependency:** directed producer→consumer relation with confidence and status `proposed`, `validated`, `executable`, `rejected`. In v0.2.1 new edges default to proposed; explicit validation uses an extensible RelationSpec registry. Legacy validated declarations are rechecked against semantic contracts; runtime postconditions and related hard constraints determine executable status. A hypothesized edge is *not* asserted to be a true causal relation.
- **Constraint:** spatial/topological rule evaluated over generated artifacts, with node references, thresholds/units, and hard/soft designation. Constraints can involve multiple regions.
- **Evidence:** image, camera, depth, manual or derived source; `observed/inferred/generated` status and uncertainty. A reference view anchors observed constraints; hidden regions may have competing valid hypotheses.
- **Scene world:** local metric frame, z-up, stable IDs and explicit coordinate conversions at adapter boundaries.

A procedural program is an operator composition graph (not simply a Scene Graph that lists objects). Its operator contract should specify input/output types, preconditions, parameter units, deterministic randomness, resource limits and validators. Future Agent-proposed operators must pass syntax checks, sandbox policy, smoke tests and geometric validators before registration.

## 4. Proposed algorithm

**Stage A — image evidence:** camera geometry and depth priors (MoGe; optional Building Rome), image segments, semantic regions, important object instances, confidence maps. With multiple views, aggregate evidence in a common world frame; track whether the images represent a geometrically coherent world.

**Stage B — generative graph induction:** an Agent proposes regional programs, instance vs field representations, and dependency hypotheses. Grounded operations are constrained to registered verified primitives by default; for unexpressible cases, the Agent may propose new code, but it runs only after isolated validation. Store alternatives for uncertain hidden structures.

**Stage C — joint geometric/procedural reconstruction:** execute the program graph, render from calibrated reference cameras, compare visual evidence, check global geometry/topology constraints and spatial-statistical properties. Iterate over program structure *and* continuous parameters; prefer local deterministic optimization over repeated arbitrary code rewrites.

**Stage D — dependency-preserving edits:** convert a user edit into typed changes; compute affected subgraph of verified dependencies; re-execute only necessary nodes; evaluate hard constraints, solve or change program structure if required, and keep unaffected regions fixed. If a hypothesis is uncertain, request additional view evidence or retain alternatives rather than silently marking it verified.

**Stage E — optional extension:** apply learned/stochastic programs in unseen surrounding regions, subject to shared boundary and topology constraints. Extension is supporting evidence, not the primary paper contribution.

## 5. v0.1 executable contract

The attached code intentionally implements only the **smallest testable substrate**:

- Engine-neutral JSON IR (`Scene`, `Node`, `Dependency`, `Constraint`, `Evidence`).
- Cycle/missing-ID checks and explicit uncertainty statuses.
- Simple registered proxy operators: `terrain.heightfield`, `road.polyline`, `vegetation.scatter`, `building.proxy`, `background.constant`.
- Verified dependency traversal and deterministic partial regeneration after a local heightfield edit.
- Checks for building ground contact, road ground contact, road grade, tree–road clearance, and a minimum population count.
- A **synthetic hand-authored** 200 m scene for a smoke test — no actual image understanding, Blender rendering, cross-region constraint solver, or Agent structural search yet.

**Crucial limitation:** detecting a violation is not solving it. Automated constrained repair, multi-region terrain continuity, actual camera-grounded reconstruction, uncertainty update and verified new-operator code synthesis are next milestones. Treat this code as architecture scaffolding, never as proof of claimed reconstruction capability.

## 6. First experiment: one scene, two edits

Synthetic ~200 × 200 m terrain with a road, an individual building, trees and a separate unaffected environment node. Obtain rendered reference images from a ground-truth Blender scene as soon as the Blender adapter exists.

- **Edit 1, terrain:** add a localized height change near a road/building; recompute roads, ground contact and scatter. Test that unrelated content is bitwise preserved and constraints remain satisfied or explicitly fail.
- **Edit 2, road:** widen/shift a road, propagate exclusions to vegetation and ground-level transitions. Test connectivity and object-contact preservation once topology validator exists.

### Controls/ablations

| Setting | Program structure | Dependencies | Global validation |
| --- | --- | --- | --- |
| Fixed template | Predefined | Independent per region | No |
| Program agent | Agent-induced | None | Local render-only |
| Fixed constraints | Predefined | Validated edge graph | Yes |
| Ours | Agent-induced/edited | Inferred and validated | Yes |

Include fixed-rule and solver baselines so any improvement cannot be explained by simply adding more tools or more LLM tokens.

### Evaluation metrics

1. **Observed reconstruction:** reference image feature/semantic match and visible-surface geometry.
2. **Held-out and hidden regions:** rendering at independent cameras, 3D completeness, and plausibility rather than claiming uniquely correct occluded geometry.
3. **Global spatial correctness:** road topology, support/contact error, terrain boundaries, area overlap/violations, scene-scale drift.
4. **Edit fidelity:** edit goal satisfaction, post-edit constraint violation rate, preserved-region difference, affected node count, regeneration latency and API/GPU cost.
5. **Program quality:** structural correctness, reproducibility, number of invalid Agent proposals, number of truly novel operator structures selected beyond a fixed template.

A representation with only a scene graph but no measurable reconstruction/editing gains should not be presented as a strong standalone method.

## 7. Engineering split — one researcher + Codex assistants

- **IR/constraint assistant:** typed schema and dependency validator; structural edits and uncertainty bookkeeping.
- **Executor assistant:** Blender adapter and later Infinigen integration; stable camera, units, object IDs.
- **Benchmark assistant:** GT scene renderer, standardized edits, metrics, reproducible seeds.
- **Researcher:** program induction and constraint compilation algorithm, baselines, ablations and paper claims.

Agents should use a common trace format (model, version, input/prompt, tool calls, timestamps, seed, cost and output). Adapter must support replaceable model backends; an isolated Codex CLI runtime is permitted when model APIs fail but must be reported as a different experimental backend.

## 8. Go/no-go checkpoints

- **Week 1:** v0.1 proxy IR and propagation pass all tests (provided scaffold).
- **Weeks 2–3:** real image evidence is mapped into common coordinates; Blender outputs initial/edited proxy renders and stable IR.
- **Weeks 4–5:** show dependency-aware global refinement and edit with measurable improvement over fixed rules on at least three mixed-scene examples.
- **If no improvement:** narrow to a concrete cross-type geometric/topological task, rather than expanding tool integration.
- **Months 2–4:** high-fidelity adapters, full dataset, image+multi-view experiments, uncertainty studies, ablations and paper.

## 9. Baseline integration policy

Reuse existing independently reproduced MoGe, Building Rome, RCWM, fal-worldclaw, UrbanWorld2.0 and Infinigen through *adapters*, one at a time. Keep external repositories, large datasets and model weights out of the new Git history. Record upstream versions, license restrictions and API assumptions. Favor Blender as primary renderer, engine-neutral IR as scientific output and Unreal as optional secondary demonstration.

# Unified execution and operator API

OutdoorGraph 0.2.1 uses `outdoorgraph.execution.Planner` for proxy and native
Blender execution. Registration supplies Python callables from trusted project
code; Scene IR names an operator but cannot inject arbitrary source code.
This registration boundary is not a security sandbox.

## Contracts

`OperatorSpec(name, run, input_slots, output_kinds, version="1", backend="proxy",
preconditions=(), postconditions=(), cache_validator=None)` declares one operator.
`InputSlot(kinds, required=True, multiple=False, relations=None)` declares an
input port. The current artifact output port is `result` (the output kind is
also accepted as a legacy alias); arbitrary undeclared outputs are rejected.

`run(node, inputs, context)` receives artifacts bound by **port name**, not fixed
node IDs. Multiple ports receive a list, including an empty optional list.
`context["input_bindings"]` maps ports to source IDs. The run callback receives
the computed `signature`. Preconditions run before signature serialization and
must not depend on it. Preconditions return False or raise to reject execution;
postconditions receive `(node, artifact, inputs, context)` and do the same.
Every artifact must be finite JSON with the declared `kind`; backend-specific
postconditions check required geometry and real resources.

```python
from outdoorgraph.execution import InputSlot, OperatorSpec, Planner, execute, proxy_registry

registry = proxy_registry()
registry.register(OperatorSpec(
    name="my_adapter.supported_asset",
    run=run_existing_generator,  # application-owned callable / subprocess adapter
    input_slots={"terrain": InputSlot(("terrain",))},
    output_kinds=("building",),
    version="installed-generator-release", backend="external",
    postconditions=(check_actual_generated_asset,),
    cache_validator=check_asset_still_exists,
))
plan = Planner(scene, registry).plan()
result = execute(scene, previous=previous.artifacts, registry=registry)
if not result.passed:
    save_failure_report(result.to_dict())
```

The external adapter owns its subprocess, logs and source/config provenance. A
unit-test adapter is not evidence that its external model has run. The supplied
Blender registry genuinely calls bpy and native Infinigen inside the same
topological execution loop. The existing MoGe process remains independent;
its generated surface is consumed as an external artifact by this registry.

## Dependency lifecycle and evidence

Scene schema remains `0.1`. Dependencies add optional `input_port` and
`output_port`; `executable` is an additional accepted lifecycle status. Old
fields/IDs are preserved. `Scene.valid_edges()` lists candidate runtime edges;
it is not an execution permit. Callers execute through `Planner`.

1. `proposed`: observational or semantic hypothesis. Confidence is descriptive.
2. `Planner(scene, registry).validate_dependencies()` explicitly attempts typed
   proposal validation. Existing nodes, output/input ports, kinds, required
   inputs and executable topology must pass. The returned object contains
   `.scene`, `.dependency_reports`, `.errors` and `.passed`. Unknown semantics
   remain proposed. Complete valid typed proposals become `validated`.
3. `Planner(...).plan()` rechecks even old validated/executable declarations;
   it reports bindings and errors. Known relations cover height sampling,
   support, road exclusion and reconstructed-surface scatter. A registered
   input may explicitly allow additional relation names.
4. Execution checks actual upstream outputs, preconditions and postconditions.
   Related hard constraints are evaluated as soon as their artifacts exist;
   failure blocks affected downstream work. Final lifecycle evidence marks
   successful edges executable, or keeps them validated/rejected/proposed with
   reasons. Missing geometry evidence is never presented as an observed fact.

Target geometry often exists only after generation. The runtime therefore
distinguishes a passed input/structural gate from successful completion of
geometry/postcondition gates. An intermediate generated artifact is not a
successful committed scene: failed runs return `passed=False`, preserve reports
and exit nonzero in the CLI. There is no automatic constraint repair.

Geometric checks include ground contact, dense road contact, slope, vegetation
clearance and population. Topology checks cover executable DAG cycles, road
sampling/connectivity/endpoints and shared terrain boundary continuity. These
are finite checks of the stated representation, not general physical proofs.

## Automatic caching and edits

`execute(scene, previous=...)` accepts a prior `RunResult` or its artifacts map.
The legacy `outdoorgraph.runtime.execute` forwards to it while retaining old
exception behavior. No dirty-node hint is required. Optional `changed_ids` is a
conservative explicit invalidation hint for existing callers.

Signatures include operator name/release/backend, parameters, bounds, seed,
metadata/evidence references, source IDs, ports, relations and upstream result
IDs. Result IDs also describe generated JSON output. Removing, adding or
rewiring dependencies changes the affected consumer signature. Changed
producers propagate through the bound graph. Missing/unsigned/failed artifacts
or missing backend resources regenerate safely. Operator implementations must
update their declared release when their behavior changes; source files are not
hashed as a substitute for that contract.

`result.to_dict()` saves artifacts, `rebuilt_ids`/`preserved_ids`, per-node
statuses/timing/reasons, cache signatures/result IDs, dependency evidence,
constraint reports, failures and migrated Scene IR. No file-integrity hash
system, database or extra schema version is required.

## Real Blender resources

`graph_blender_worker.py` registers:

| Operator | Input ports | Output kind |
|---|---|---|
| `blender.terrain` | `heightfield: terrain` | terrain |
| `blender.road` | `terrain: terrain` | road |
| `blender.grass` | `terrain: terrain`, optional multiple `roads: road` | vegetation |
| `blender.object` | `terrain: terrain` | building |
| `external.moge.mesh` | existing bundle asset | terrain |
| `external.infinigen.grass` | `terrain: terrain`, optional roads | vegetation |

The first operator discretizes the existing heightfield; roads use the existing
polyline operator and project a dense ribbon onto actual terrain triangles.
Grass calls native `GrassTuftFactory`, `make_asset_collection` and
`scatter_instances`; OutdoorGraph only supplies the selection/exclusion field.
Each grass node owns a private support snapshot, so preserved Geometry Nodes
do not retain references to a deleted neighboring terrain object.

Backend nodes own collections and stable object IDs derived from their node ID
and role. Rebuilding one collection does not delete preserved collections.
Unchanged instance transforms and metadata are compared directly. Grass snapshot
instance IDs are deterministic within a fixed seed/configuration; editing
density/region does not promise one-to-one identity for the regenerated grass.

`run_graph(scene, config, out, previous=None)` in
`outdoorgraph.adapters.graph_pipeline` launches Blender and then a separate
reopen/GLB-import verifier. It saves exact configuration, commands, logs,
`state.json`, `scene_ir.json`, `dependency_graph.{json,svg}`, geometry checks,
RGB, BLEND and GLB. `out` must be new; previous scenes remain available.

## Future induction caller

An induction module can create `Scene` nodes/dependency proposals, call typed
validation, inspect rejected ports/constraints, select registered operators,
execute with prior artifacts and consume RGB plus machine-readable failures.
It can edit parameters or graph edges without rebuilding unrelated regions.
Operator synthesis, semantic induction, uncertainty optimization and global
constraint solving are not implemented by this API.

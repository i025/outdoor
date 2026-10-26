# Adapter boundary (v0.2)

Implemented adapters use JSON/NPZ/PNG files and separate processes. Read
`docs/adapter_interface.md` and `docs/integration_inventory.md` before adding a
backend. Renderer-specific state remains outside the core Scene IR:

- `moge_worker.py`: existing MoGe v2 inference API, camera/depth/point evidence.
- `building_rome`: optional mesh prior, with clear observed/inferred surface labels.
- `blender_worker.py`: imported reconstruction, native Infinigen GrassTuft/Scatter,
  calibrated and local-view rendering, editable BLEND and GLB with provenance.
- `blender_verify.py`: independent scene reopen, mask/contact/calibration checks,
  decoded renders and actual GLB import. No model/render result is mocked.
- `urbanworld`: city assets and feature import.
- `fal_worldclaw`: optional agent planning / procedural UI tool adapter.

`contracts.py` defines SceneBundle 0.2 around the unchanged Scene 0.1 schema;
`pipeline.py` records exact process execution and propagates backend failures.
Stable metric z-up coordinates, IDs, seeds, camera conventions, manual evidence
and upstream provenance are required. City/planner/alternative geometry backends
remain separate plans; they are not silently included by this integration.

## Unified v0.2.1 execution

`graph_pipeline.py` launches the existing backend; `graph_blender_worker.py` imports
the same Planner and registers native operators. The legacy `blender_worker.py` CLI
bridges to that registry. See [execution API](../../../docs/execution_api.md) and
[real editing validation](../../../docs/integration_validation_v0.2.1.md).

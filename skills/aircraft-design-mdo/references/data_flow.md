# Data Flow & Discipline Coupling

> Information dependencies between the five MCP servers for the DLR-F25
> MDO benchmark. This file is the source of truth for what the
> data-plane middleware captures, where it injects, and what triggers
> each transfer. It is loaded into the orchestrator's system prompt at
> runtime by the orchestrated strategy.
>
> A different design task lives in a different version of this file —
> the orchestrator's prompt itself stays generic.

## TL;DR — The Active Couplings

The framework's data plane (`src/tools/data_plane.py`) intercepts MCP
tool responses and requests transparently. The orchestrator and workers
do NOT pass these values manually; the framework moves them
cross-discipline automatically AS LONG AS the producing tool runs
before the consuming tool.

| # | Captured value | Capture trigger | Injection target | Injected on | Status |
|---|---|---|---|---|---|
| H-CL | `CL_CRUISE` | SU2 `read_history_csv` | `Mission.Design.LIFT_COEFFICIENT` | aviary `set_aircraft_parameters` | active |
| H-CD | `CD_CRUISE` | SU2 `read_history_csv` | `Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR` | aviary `set_aircraft_parameters` | active |
| K-A | `mWing_kg` | mass-mcp `estimate_mass` | `Aircraft.Wing.MASS_SCALER = mWing_kg / 5998`, clamped [0.5, 2.0] | aviary `set_aircraft_parameters` | active |
| K-B | `mTOM_kg` | mass-mcp `estimate_mass` | `Fn_DES_lbf = MTOM_kg × 0.0811`, clamped [2000, 25000] | pyCycle `set_inputs` (gated on `mcp_name == "pycycle"`) | active |
| J | `perf.TSFC` | pyCycle `get_outputs` | `Aircraft.Engine.SUBSONIC_FUEL_FLOW_SCALER` | aviary `set_aircraft_parameters` | **REVERTED — no-op** (aviary's FwFm tabular engine deck ignores this scaler; SFC currently has zero downstream effect) |

## What this means for the orchestrator

The orchestrator's job when delegating is to make sure the **capture
trigger** tool runs before the matching **injection target** tool. If
that ordering is violated, the data store is empty when the consumer
tool fires and the consumer falls back to defaults (`MASS_SCALER=1.0`,
`LIFT_COEFFICIENT` = aviary default polar, etc.). The mission run will
still complete and report a fuel number — it just won't reflect the
upstream disciplines.

**Concrete consequence:** if aviary's `set_aircraft_parameters` runs
before SU2's `read_history_csv` AND before mass-mcp's `estimate_mass`,
the resulting fuel burn is mathematically a single-MCP aviary run, not
a coupled MDO solution. Observed in wandb runs `bpkm3zm4` and
`krlbsfgx` (both produced reasonable-looking fuel numbers but with the
data plane bypassed; documented in CHANGELOG.md under 2026-05-25).

## DesignState Schema

The `DesignState` is the framework's per-session bookkeeping object:

```python
class DesignState:
    cpacs_file_path: str
    sessions: dict[str, str]          # MCP name -> active session_id
    results: dict[str, dict]          # MCP name -> latest result payload
    constraints: dict[str, dict]      # constraint_label -> {value, limit, operator, satisfied}
    iteration: int
    history: list[dict]
    data_store: dict[str, Any]        # data-plane payload + captured-coupling cache
```

The `data_store` is dual-purpose:
- Large binary payloads (volume meshes, CAD blobs) — the original data
  plane introduced in Phase A.
- Captured discipline outputs (CL, CD, mWing_kg, mTOM_kg, TSFC) — the
  capture middleware added in Phases H/I/K.

## Capture mechanism (intercept_response)

`src/tools/data_plane.py::intercept_response` fires after every MCP tool
returns. It parses the structured fields and stashes the
coupling-relevant values into the per-session `data_store`. Capture
only happens if the specific tool below is called:

- **SU2 `read_history_csv`** — last row's CL and CD columns. Strips
  embedded quote chars from the column headers (history.csv writes them
  as `"       \"CL\"       "`).
- **mass-mcp `estimate_mass`** — `components.mWing_kg` and `mTOM_kg`
  from the response. Sanity-bracketed.
- **pyCycle `get_outputs`** — `perf.TSFC`. (Capture lives on as dead
  code; injection was reverted in Phase J — see below.)

If the agent extracts CL/CD by, say, downloading the SU2 results
files via `get_result_file_base64` and parsing them locally, the
middleware never sees the values and the H couplings don't fire.
`read_history_csv` is the canonical capture path.

## Injection mechanism (resolve_request)

`src/tools/data_plane.py::resolve_request` fires before every MCP tool
sends. It merges captured values from `data_store` into the request
payload:

- **aviary `set_aircraft_parameters`** — injects
  `Mission.Design.LIFT_COEFFICIENT`,
  `Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR`, and
  `Aircraft.Wing.MASS_SCALER` from whatever's in the data store.
- **pyCycle `set_inputs`** (gated on `mcp_name == "pycycle"` to avoid
  colliding with any other MCP's same-named tool) — injects
  `Fn_DES_lbf` derived from MTOM.

Both injections are silent — they do not generate an error if the
data_store is empty; they just don't add the parameter. Aviary then
uses its own default for any uninjected parameter.

## Phase J caveat (REVERTED — pyCycle SFC has no downstream effect)

Phase J originally injected pyCycle's converged TSFC into aviary's
`Aircraft.Engine.SUBSONIC_FUEL_FLOW_SCALER`. The scaler was reverted
the same day it shipped because aviary's mission analysis uses a FwFm
*tabular* engine deck — the scaler is read but has no effect on the
table lookup, so the injection silently did nothing. Direct sensitivity
sweep confirmed: SFC ∈ {0.40, 0.55, 0.70} → fuel stayed flat at 7058.4
kg. A regression test `test_phase_j_sfc_changes_fuel_burn` (`xfail`
strict=True) guards re-addition.

Implication for the orchestrator: do NOT expect pyCycle's TSFC to flow
into aviary's mission fuel burn. The pyCycle discipline IS still
useful — Phase K-B injects MTOM-derived thrust into pyCycle's set_inputs
so pyCycle sizes itself to the design point — but pyCycle's output does
not currently feed mission analysis.

## Non-coupling transfers (large binary payloads, no middleware capture)

These are blob payloads that pass through the LLM context as
lightweight refs, resolved by the request middleware when consumed.
The orchestrator should still ensure source-before-consumer ordering,
but no per-discipline value is captured.

| Source tool | Target tool | Payload |
|---|---|---|
| TiGL `generate_volume_mesh` | SU2 `set_mesh` | base64 SU2 volume mesh, 1-50 MB. **Use this, not `export_component_mesh`** — the latter produces a surface-only mesh that fails SU2's `DistributeColoring` check. |
| TiGL `close_cpacs` (writes file) | mass-mcp `estimate_mass(cpacs_file_path=...)` | CPACS XML file on disk. mass-mcp reads it directly from the filesystem. |

## CPACS file on disk — the only shared-filesystem coupling

All other inter-MCP data flows through MCP tool return values and the
data plane. Only the CPACS file itself is shared via the filesystem.
`DesignState.cpacs_file_path` is the canonical reference.

## Pre-Phase-H history (kept for context)

Earlier versions of this document described the orchestrator manually
extracting CL/CD from SU2 results and constructing drag polars to pass
to aviary. That was the design before Phase H (2026-05-23). It is no
longer accurate — the agent does its discipline-native work and the
framework does the cross-discipline plumbing. If you are revising this
file for a different design task, decide whether the new task should
follow the same pattern (recommended) or whether some couplings should
be explicit in the agent prompt (riskier — the prompt-only approach
failed four times in Phase H Runs #13–#16).

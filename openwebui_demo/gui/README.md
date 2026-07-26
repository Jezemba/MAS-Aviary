# MAS-Aviary Multi-Agent Workflow GUI

A React + Vite + TypeScript web GUI for monitoring the MAS-Aviary multi-MCP
aircraft-design MDO pipeline (seven specialist agents across five MCP servers,
optimizing the DLR-F25 benchmark).

Five views: **Setup** (configure & launch), **Topology** (how the crew spawns
per org structure), **Live** (real-time run monitoring), **Results** (outcome vs
the F25 reference), and **Replay** (cached run playback at controllable speed).

Recreated from the design package at
`Avion/Design/Multi Agent Interface.zip` (`design_handoff_mas_aviary_gui/`).
Design tokens, layout, typography (Inter + JetBrains Mono; every number/ID/tool
name in mono), spacing, and interaction behavior are ported faithfully; the
prototype's Babel-in-browser transpile, `window`-assignment module sharing, and
hardcoded mock data are **not** carried over.

## Data flow

Both **Live** and **Replay** consume the SAME structured SSE endpoint on
`chat_server.py` - `GET /v1/runs/stream?mode=live|replay` - which folds the
MAS-Aviary event stream into the GUI's `Run` shape via
`openwebui_demo/run_reducer.py` (reusing `narration.py` for all per-tool and
per-agent copy; the frontend never duplicates that text). Live and replay differ
only in the source iterator and pacing - one code path.

```
run_batch.sh / cached log ──▶ event_parser ──▶ RunReducer ──▶ /v1/runs/stream (SSE)
   (live)         (replay)      (+ narration.py)                     │
                                                                     ▼
                                                    gui  useRunStream ──▶ Run ──▶ views
```

- **Replay** streams the bundled Run #6 log for free (no MCPs, no API cost).
- **Live** is gated behind an explicit "Connect live run" button - it spawns the
  real paid 5-MCP pipeline (~10 min, Anthropic + wandb). The GUI shows the mock
  fixture until you opt in.
- If the backend is unreachable, Replay falls back to the bundled mock fixture so
  the design still renders offline.

## Running it

There is no system Node here; a dedicated conda env holds Node 22:

```bash
export PATH="/home/aipexws3/anaconda3/envs/avion-gui/bin:$PATH"   # node 22 + npm
cd /home/aipexws3/Jessica/Avion/MAS-Aviary/openwebui_demo/gui
npm install        # first time only
npm run dev        # http://localhost:5173
```

Start the backend (in the Avion venv, which has fastapi/uvicorn) so Live/Replay
have data:

```bash
cd /home/aipexws3/Jessica/Avion/MAS-Aviary/openwebui_demo
/home/aipexws3/Jessica/Avion/.venv/bin/python chat_server.py   # http://127.0.0.1:8090
```

Point the GUI at a non-default backend with `VITE_RUN_STREAM_BASE`
(e.g. `VITE_RUN_STREAM_BASE=http://host:8090 npm run dev`).

```bash
npm run build             # type-check + production build → dist/
./node_modules/.bin/tsc --noEmit   # type-check only
```

## Layout

```
src/
  styles/      tokens.css · app.css · topology.css · fonts.css (self-hosted @fontsource)
  data/        types.ts · reference.ts (agents, real MCP tool catalog, F25 ref)
               mockRun.ts (RUN/RUN_DONE fixtures) · toolIcons.ts
  lib/         format.ts (fmtDuration/fmtTokens/fmtNum/pctDelta) · useTicker.ts
  components/  atoms · Shell · ProgressStrip · TokenBudget · StageCard · PendingStrip · CodeBlock
  sse/         useRunStream.ts (SSE consumer)
  views/       SetupView · TopologyView · LiveView · ResultsView · ReplayView
  App.tsx      view routing + client state + preferences
```

## Notes / v1 scope

- v1 renders `sequential × iterative_feedback` end-to-end; the other coordination
  cells are present in the coverage matrix but greyed out.
- The MCP tool chips use the **real** tool names (validated against
  `narration.py` and `skills/aircraft-design-mdo/references/tool_catalog.md`), not
  the prototype's simplified placeholders.
- Runaway-loop guard: a stage that re-invokes more than 6× collapses its stacked
  attempts into an accordion with an amber warning and a "Stop this stage" action.
- `prefers-reduced-motion` disables spinners, the blackboard pulse, and the
  topology spawn stagger (elements appear immediately).
- Per-tool durations aren't in the runner stdout (only per-step); the reducer
  splits each step's duration across its tools as an estimate.

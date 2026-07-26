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

## Live runs

The Live view's "Connect live run" spawns the real pipeline via
`chat_server` → `live_runner.py` → `run_batch.sh`. The Setup form drives the run:
**combo** (structure × handler), **repeats**, **timeout**, and **seed** map to
real runner CLI flags. All 8 valid combinations run (this branch is built on
`feat/phase-l-all-combos`). Model and the initial AREA/AR/scale fields are set in
the config YAML and are not CLI-parametrized, so they don't affect a live run.
Requirements: the 5 MCP servers up and `ANTHROPIC_API_KEY` with credit; a run
costs ~$0.30-1.50 and takes 6-20 min.

**Live runs are token-gated (secure by default).** The server refuses every
live request unless `MAS_LIVE_TOKEN` is set in its environment; then each request
must send that token (the GUI prompts for it and sends it as the `X-Live-Token`
header — it is never baked into the static build). So an exposed/tunneled backend
without the env var **cannot** be made to spend money, and with it, only someone
who knows the token can. Replay is free (no LLM) and always open. Enable live
locally with `MAS_LIVE_TOKEN=... .venv/bin/python chat_server.py`.

## Hosting (GitHub Pages + tunneled backend)

Pages is static-only, so the deployed site is the frontend; live runs and real
streaming need the Python backend reachable over the network.

**One-time setup**
1. Repo → **Settings → Pages → Source: GitHub Actions**.
2. Push to `feat/mas-gui-full` (or run the workflow manually). The
   [`deploy-gui.yml`](../../.github/workflows/deploy-gui.yml) action builds the
   GUI with `VITE_BASE=/<repo>/` and deploys it to
   `https://<user>.github.io/<repo>/`.

**To make Live / real Replay work on the hosted site** (otherwise it falls back
to the bundled mock data):
1. Run the backend locally: `.venv/bin/python chat_server.py` (:8090).
2. Expose it with a tunnel, e.g. a Cloudflare quick tunnel:
   `cloudflared tunnel --url http://127.0.0.1:8090` → copy the
   `https://xxxx.trycloudflare.com` URL.
3. Repo → **Settings → Secrets and variables → Actions → Variables** → add
   `VITE_RUN_STREAM_BASE` = that tunnel URL, then re-run the deploy workflow.
4. The backend already sends permissive CORS, so the Pages origin can reach it.
   Real Replay works whenever your backend is up; Live also needs the 5 MCPs +
   API credit.

## Notes

- The MCP tool chips use the **real** tool names (validated against
  `narration.py` and `skills/aircraft-design-mdo/references/tool_catalog.md`), not
  the prototype's simplified placeholders.
- Runaway-loop guard: a stage that re-invokes more than 6× collapses its stacked
  attempts into an accordion with an amber warning and a "Stop this stage" action.
- `prefers-reduced-motion` disables spinners, the blackboard pulse, and the
  topology spawn stagger (elements appear immediately).
- Per-tool durations aren't in the runner stdout (only per-step); the reducer
  splits each step's duration across its tools as an estimate.

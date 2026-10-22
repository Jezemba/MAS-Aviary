# Run viewer

Plays back a MAS-Aviary MDO run link by link, agent step by agent step, following Jessica's
mockups (`Avion/UI/*.png`). It follows a live run as its files grow. It only reads run folders
and never calls an MCP server.

## Pieces

| Path | What it does |
|---|---|
| `extract/snapshot.py` | Copies the aviary recorder (`aviary_<sid>_out/problem_history.db`) and the SU2 surface files of each link into a work folder. These live outside the run folder and get cleaned up. |
| `extract/build.py` | Builds one run (output dir + combination) into a bundle: `run.json` plus one JSON per link, and images. Rebuilds are incremental. |
| `extract/helpers/` | `aviary_trajectory.py` (aviary env) and `cpacs_to_stl.py` (tigl env), run as subprocesses. |
| `extract/images.py` | Aircraft top/front view from the TiGL STL; SU2 surface Cp from `surface_flow.csv`. Axes and colour range are fixed so N and N-1 compare. |
| `server/serve.py` | Serves the app and the bundles on 127.0.0.1, and rebuilds live runs every `--live-interval` s. |
| `web/` | React + Vite app. |
| `runs.example.json` | Catalog format: one entry per run, `live: true` for a run in progress. |

## Run it (aipexws8)

```bash
cd ~/Jessica/run_viewer_work/app            # rsync of this folder
PATH=~/Jessica/tools/node-v22.11.0-linux-arm64/bin:$PATH
(cd run_viewer/web && npm ci && npm run build)
~/Jessica/Avion/.venv/bin/python -m run_viewer.extract.snapshot --run-dir <run dir> --out ../snapshots --aviary-dir ~/Jessica/Avion/aviary-mcp
setsid nohup ~/Jessica/Avion/.venv/bin/python -m run_viewer.server.serve \
    --catalog ../runs.json --bundles ../bundles --port 5173 --build > ../logs/serve.log 2>&1 &
```

To view it from your machine: `ssh -i ~/.ssh/voir_shard -L 5173:127.0.0.1:5173 aipexws8@172.24.49.250`,
then open http://localhost:5173. Stop the server by its process group: `kill -TERM -<pgid>`.

## Publish (static site, data embedded)

```bash
(cd run_viewer/web && npm run build:static)
python -m run_viewer.export_static --catalog ../runs.json --bundles ../bundles \
    --web run_viewer/web/dist-static --out ../site_release_<date> --commit "<this repo's commit>"
```

The folder works on GitHub Pages (its own workflow), as a Hugging Face static Space, or on any web
server. Published 2026-10-22 at github.com/Jezemba/mas-aviary-run-viewer
(www.jessicaezemba.com/mas-aviary-run-viewer/) and huggingface.co/spaces/JessicaE/mas-aviary-run-viewer.
To update: export into a new folder, replace the site repo's contents with it, and push. For the Space,
run `HfApi().upload_folder(repo_id="JessicaE/mas-aviary-run-viewer", repo_type="space", folder_path=...,
ignore_patterns=[".github/*"])`.

## Tests

```bash
env -u ANTHROPIC_API_KEY ~/Jessica/Avion/.venv/bin/python -m pytest -q run_viewer/tests/test_build.py
(cd run_viewer/web && npx vitest run)
<venv with playwright>/bin/python run_viewer/tests/ui_smoke.py http://127.0.0.1:5173 qa/shots   # server running
```

## What the panels show, and where it comes from

- **Current agent:** step log rows. The context counter is the generation's `prompt_tokens` against
  `PROMPT_BUDGET_TOKENS` (30000). Networked batched generations record no prompt, and the counter says so.
  Do not use `result.json` `total_tokens`: it is a placeholder for local models.
- **Images:**
  - Aircraft: `geometry_end.xml`, or the inherited geometry when the link did not change it.
  - SU2: the link's last successful solve.
  - Mission: aviary's recorder (the session's last simulation).

  SU2 and the mission appear once playback passes the call that produced them. A link that did not
  fly has no plotted trajectory.
- **Metrics:**
  - Link-level series come from `result.json`.
  - Coordination metrics are that combination's `org_theory_metrics`.
  - Running per-step values are recomputed from the step log with the repo's formulas (counts,
    misroute rate, stage completion, attempts).
- **Parameters:** N is the previous link's end state (link 1: `initial_params`); N+1 is this link's
  `design.json` / `chain_end_params`. Green means the value went up, red down, black unchanged, grey
  missing.
- **Communication:**
  - Orchestrated: `create_agent`, `assign_task`, `message_orchestrator`, `reply_to_agent`, ...
  - Networked: the board tools exactly as called; `mark_todo_failed` is how a claim is released.
- **Strip:**
  - Staged pipeline: the stages of the link.
  - Iterative feedback: agents appended as they come up, repeats shown as ×n.
  - Graph-routed: the route as nodes, with backward moves in red. For networked runs the nodes are
    the claimed TODO roles.

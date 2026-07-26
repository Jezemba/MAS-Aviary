# MAS-Aviary Open WebUI Demo

A chat-style demo of the multi-MCP MDO pipeline. The user types any
prompt; the system kicks off the DLR-F25 design task and streams each
agent's tool calls back into the chat window as collapsible
color-coded cards.

## What you see

```
🛩️  MAS-Aviary multi-MCP MDO - DLR-F25
Design task: 2500 nmi / 200 pax / Mach 0.78 / FL330

📐 Geometry Engineer (tigl-mcp) - running…
  Step 1
    🔧 open_cpacs(...)
       ↩ session da272edf…
       ⏱ 2.5 s
    Step 2
    🔧 get_configuration_summary(...)
    ...
  Final answer:
    DESIGN_STATE: ...
  Done in 60 s - 9 steps, 9 tool calls.

💨 Aerodynamics Analyst (su2-mcp) - running…
    ...

⚖️ Structures Analyst (mass-mcp) ...
🔥 Propulsion Analyst (pycycle-mcp) ...
✈️ Mission Architect (aviary-mcp) ...
📊 Simulation Executor (aviary-mcp) ...
🧮 MDO Integrator ...

🎉 Run complete - 1 completed, 0 failed.
```

Each `<details>` block is collapsible - clicking the header toggles
the tool-call detail. The header itself stays color-coded by agent.

## Two frontends, one backend

This directory serves two frontends off the same event source:

1. **Open WebUI chat demo** (the markdown cards above) - `/v1/chat/completions`.
2. **The React workflow GUI** in [`gui/`](gui/) - a five-view dashboard (Setup,
   Topology, Live, Results, Replay) built from the design package. It consumes a
   structured `Run`-snapshot SSE stream at **`GET /v1/runs/stream?mode=live|replay`**,
   folded from the same `event_parser` events by `run_reducer.py` (reusing
   `narration.py` for all copy). Both frontends share one live/replay code path.
   See [`gui/README.md`](gui/README.md) to run it.

## Two modes

| Model dropdown choice | What it does                                              | Cost / time                 |
|-----------------------|-----------------------------------------------------------|-----------------------------|
| `mas-aviary-replay`   | Replays Run #6's saved transcript at ~10× realtime        | Free, ~30 s                 |
| `mas-aviary-live`     | Spawns `./run_batch.sh` and streams the real 5-MCP run    | Anthropic + WandB, ~10 min |

You pick the model from Open WebUI's model selector in the top-left
of the chat window.

## Quick start

Prereqs:
- Docker + docker-compose installed (`docker --version`, `docker-compose --version`)
- The MAS-Aviary `.venv` already exists at `../../.venv/` with `fastapi` and `uvicorn` installed:
  ```
  ../../.venv/bin/pip install "fastapi>=0.115" "uvicorn[standard]>=0.32"
  ```
- For live mode: the 5 MCP servers running on their default ports
  (`tigl:8500`, `su2:8200`, `mass:8700`, `pycycle:8400`, `aviary:8600`),
  and `MAS-Aviary/.env` with `ANTHROPIC_API_KEY` set.

From this directory:
```
./start_demo.sh
```
Opens nothing automatically - visit `http://localhost:3000` in your
browser. Pick `mas-aviary-replay` from the model dropdown, type any
prompt, hit send. Watch the cards stream in.

To tear down:
```
./stop_demo.sh
```

## Architecture

```
┌─────────────────┐  HTTP /v1/chat/completions   ┌──────────────────────┐
│  Open WebUI     │ ◄──────────────────────────── │  chat_server.py      │
│  (Docker :3000) │  SSE stream of OpenAI-format  │  (host :8090)        │
└─────────────────┘  chat.completion.chunk        │  - FastAPI           │
                                                  │  - /v1/models        │
                                                  │  - /v1/chat/completi.│
                                                  └─────────┬────────────┘
                                                            │
                                       ┌────────────────────┼────────────────────┐
                                       ▼                    ▼                    ▼
                                  live_runner.py      replay_runner.py     event_parser.py
                                  (spawns             (reads cached        (stream parser:
                                   run_batch.sh,      Run #6 .output       lines → Events)
                                   tails stdout)      at 10× speed)              │
                                       └──────┬─────────────┘                    │
                                              │                                  │
                                              ▼                                  ▼
                                                       formatter.py
                                                       (Event → markdown
                                                        with cards + emoji)
```

## Files

| File                 | Role                                                        |
|----------------------|-------------------------------------------------------------|
| `chat_server.py`     | FastAPI service. OpenAI-compatible `/v1/chat/completions`. |
| `event_parser.py`    | Stream parser: runner stdout → `Event` objects.            |
| `formatter.py`       | `Event` → Open WebUI-friendly markdown chunks.             |
| `live_runner.py`     | Spawns `run_batch.sh`, tails stdout, yields `Event`s.      |
| `replay_runner.py`   | Reads cached Run #6 log, yields `Event`s with pacing.      |
| `docker-compose.yml` | Open WebUI container, wired to the host chat server.       |
| `start_demo.sh`      | Bring everything up.                                       |
| `stop_demo.sh`       | Bring everything down.                                     |
| `README.md`          | This file.                                                 |

## Customization

* **Different cached run** - set `DEFAULT_REPLAY_LOG` in `replay_runner.py` to
  point at any saved `run_batch.sh` output log.
* **Slower/faster replay** - change the `speed=10.0` default in
  `chat_server.py::_event_stream_from_model`.
* **Different colors / emoji** - edit `AGENT_STYLE` in `formatter.py`.
* **Custom F25 task** - currently the user prompt is ignored and the F25
  spec is fixed (per design). To accept the prompt, change
  `_event_stream_from_model` to parse `body['messages'][-1]['content']`
  and pass it to the runner via `--task` (would require a
  `stat_batch_runner.py` flag addition).

## Troubleshooting

**Open WebUI starts but "No models" appears**
  The container can't reach the host. Check:
    * `chat_server.py` is alive: `curl http://localhost:8090/v1/models`
    * From inside the container:
      `docker exec mas-aviary-openwebui curl http://host.docker.internal:8090/v1/models`
    * Firewall isn't blocking the loopback gateway.

**Live mode immediately errors**
  Likely `.env` is missing or the MCP servers aren't up. Run:
    * `cat ../.env | grep ANTHROPIC` (without printing the key)
    * `for p in 8200 8400 8500 8600 8700; do ss -tlnp | grep ":$p"; done`
  Replay mode does not need either.

**Stream cuts off mid-run**
  Open WebUI's default request timeout is generous but the host's
  reverse proxy or browser might cut SSE early. The chat_server sets
  `X-Accel-Buffering: no`. Check the chat_server log at
  `/tmp/mas_aviary_chat_server.log`.

# Networked-run visualizations

HTML pages produced by [`scripts/visualize_run.py`](../scripts/visualize_run.py).
Each file is a self-contained static page (CSS + inline JSON-as-HTML, no
external assets) showing the blackboard timeline for one
`mdo_f25_networked_iterative_feedback` pipeline run.

Open any of these directly in a browser — no server needed:

| File | Run | Notes |
|---|---|---|
| [`run_gi0kt8cr.html`](./run_gi0kt8cr.html) | wandb [`gi0kt8cr`](https://wandb.ai/jessicae/mas-aviary-stat/runs/gi0kt8cr) | **Post-attribution-fix verification run** (Job 1, 2026-05-25). Fuel = 11,974.55 kg (-1.0% vs F25 spec). Every rejected `claim_todo` event is correctly attributed to the caller (not the winner named in the message). |
| [`run_4o22y281.html`](./run_4o22y281.html) | wandb [`4o22y281`](https://wandb.ai/jessicae/mas-aviary-stat/runs/4o22y281) | post-extractor-fix concurrent_blackboard rerun. wandb correctly reports fuel = 11,615.86 kg. 5 of 7 TODOs marked done. Pre-attribution-fix log — rejected `claim_todo` events are mis-attributed to the winner (see "Attribution caveat"). |
| [`run_clogb51u.html`](./run_clogb51u.html) | wandb [`clogb51u`](https://wandb.ai/jessicae/mas-aviary-stat/runs/clogb51u) | first concurrent_blackboard run (pre-extractor-fix). Includes `agent_4` which was dynamically spawned mid-run via `spawn_peer`. Pre-attribution-fix log — same caveat. |

## What you see

- **Header bar**: wandb link, reported fuel, per-agent activity-count chips,
  and the slowest-peer wall-clock total.
- **Parallel-execution swim lanes** (top section, added 2026-05-25): one
  horizontal lane per peer with colored blocks for every tool call
  (claim / read / write / MCP discipline work) sized by step duration
  and positioned by that peer's cumulative wall-clock. Three lanes with
  activity at the same X-axis range proves the peers were executing in
  parallel — overlap in horizontal position = concurrent execution.
  Hover any block for the step number, duration, tool name, and the
  truncated observation. Axis ticks every ~20 seconds.
- **Final TODO board** (left panel): parsed from the last `read_todos`
  observation in the log. Shows which peer claimed and/or completed each
  of the 7 disciplines.
- **Tool usage breakdown** (left panel): how many times each peer-coordination
  tool fired (`read_blackboard`, `write_blackboard`, `claim_todo`,
  `mark_todo_done`, etc.).
- **Filters** (left panel): hide/show events per agent.
- **Timeline** (right panel): every blackboard read / write / claim /
  done event in chronological order, color-coded by peer. Each event row
  shows the action type, the target TODO or key, whether the call
  succeeded, the truncated observation from the tool response, and the
  source log line.

## Regenerating

```bash
PYTHONPATH=. python scripts/visualize_run.py <log_file>
# default log: /tmp/pipeline_mdo_f25_networked_concurrent_v2.log
# default output: viz/run_<wandb-run-id>.html
```

## Attribution caveat

Concurrent peer threads can interleave their smolagents stdout output at
the line level. The parser attributes each tool call to a peer using
this priority:

1. **Structured `attempted_by` field** in the JSON response (added
   2026-05-25). The `claim_todo` / `mark_todo_done` / `mark_todo_failed`
   tools wrap their response with the caller's `agent_name` so a rejected
   claim attributes to the CALLER, not the winner named in the message.
2. **Inline `'agent_X'` mention** in the response — used as a fallback
   for legacy logs (pre-2026-05-25) and for tools that name the acting
   agent inline (e.g. `read_todos` rendering).
3. **`write_blackboard` key prefix** (`agent_<N>_status` etc.).
4. **Token-signature match** (swim-lane parser, added 2026-05-25 with
   Job 2): for MCP tool calls with no inline attribution (open_cpacs,
   run_simulation, set_aircraft_parameters, etc.), pair the block's
   `(step_num, input_tokens)` against the inline-attributed peers'
   signatures. Each peer accumulates a slightly different token count
   by step N because their prompt contexts differ marginally, so the
   closest token match reliably identifies the peer from step 2 onward.
5. **Most-recent `New run - agent_X` banner** in the log — used for
   `read_blackboard` and other tools whose response carries no peer
   identity. This is the fuzziest signal under heavy thread interleave.

Pre-2026-05-25 logs (including `run_4o22y281.html` and `run_clogb51u.html`)
don't carry `attempted_by` in their `claim_todo` responses, so rejected
claims fall back to rule 2 and show up attributed to the WINNER of the
race instead of the caller in the chronological timeline. This makes
that view read as if the same peer rejected itself; in reality three
peers raced for the same TODO and the rejection messages name the winner.
The swim-lane view at the top uses both rules 1+4 and is less affected
because the token-signature match disambiguates many cases. Runs
captured AFTER the fix render correctly in both views.

The action itself and the observation text are always correct.

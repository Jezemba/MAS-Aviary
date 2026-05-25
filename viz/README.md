# Networked-run visualizations

HTML pages produced by [`scripts/visualize_run.py`](../scripts/visualize_run.py).
Each file is a self-contained static page (CSS + inline JSON-as-HTML, no
external assets) showing the blackboard timeline for one
`mdo_f25_networked_iterative_feedback` pipeline run.

Open any of these directly in a browser — no server needed:

| File | Run | Notes |
|---|---|---|
| [`run_4o22y281.html`](./run_4o22y281.html) | wandb [`4o22y281`](https://wandb.ai/jessicae/mas-aviary-stat/runs/4o22y281) | post-extractor-fix concurrent_blackboard rerun. wandb correctly reports fuel = 11,615.86 kg. 5 of 7 TODOs marked done. |
| [`run_clogb51u.html`](./run_clogb51u.html) | wandb [`clogb51u`](https://wandb.ai/jessicae/mas-aviary-stat/runs/clogb51u) | first concurrent_blackboard run (pre-extractor-fix). Includes `agent_4` which was dynamically spawned mid-run via `spawn_peer`. |

## What you see

- **Header bar**: wandb link, reported fuel, per-agent activity-count chips.
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
the line level. The parser uses two signals to attribute each tool call
to a peer: (1) explicit agent name in the tool's response message —
reliable, used for `claim_todo`, `mark_todo_done`, `mark_todo_failed`,
and `write_blackboard` when the key is prefixed with `agent_<N>_`; (2)
the most-recent `New run - agent_X` smolagents banner in the log —
heuristic, used for `read_blackboard` / `read_todos`. The action itself
and the observation text are always correct; the attribution is
occasionally fuzzy when threads have heavily interleaved output.

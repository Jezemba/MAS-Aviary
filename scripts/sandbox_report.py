"""Render a step log (and, for a sandbox run, its events) into one self-contained HTML page.

Works on any step_log_attempt*.jsonl a run writes with MAS_STEP_LOG=1, and on a stage-sandbox folder
(scripts/stage_sandbox.py), which adds events.jsonl: the exact input each stage was given, its output,
design.json after it, and -- in replay mode -- the recorded vs the replayed result of every tool call.

While the run is live the page reloads itself every 5 seconds; the sandbox re-renders it as rows arrive.

Usage:
    python scripts/sandbox_report.py <sandbox_dir>                      # writes <sandbox_dir>/report.html
    python scripts/sandbox_report.py <link_dir>/step_log_attempt1.jsonl  # writes <stem>.html next to it
"""
from __future__ import annotations

import argparse
import html
import json
import time
from pathlib import Path

CSS = """
:root { --bg:#fbfbfa; --fg:#1d1d1b; --muted:#6b6b66; --card:#ffffff; --line:#e4e2dc; --code:#f4f3ef;
        --accent:#2f5d8a; --ok:#2e7d4f; --bad:#b3261e; --warn:#9a6700; --think:#f0eefa; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
        --bg:#16171a; --fg:#e8e6e1; --muted:#9c9a94; --card:#1e2024; --line:#2e3036; --code:#24262b;
        --accent:#8db4dc; --ok:#6cc08f; --bad:#f08a80; --warn:#e0b45a; --think:#252338; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif; }
main { max-width:1100px; margin:0 auto; padding:16px; }
h1 { font-size:20px; margin:8px 0 4px; } h2 { font-size:17px; margin:28px 0 8px; border-bottom:1px solid var(--line); padding-bottom:4px; }
.meta { color:var(--muted); font-size:13px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 12px; margin:8px 0; }
.step-h { display:flex; gap:8px; align-items:baseline; flex-wrap:wrap; }
.tag { font-size:12px; padding:1px 7px; border-radius:10px; border:1px solid var(--line); color:var(--muted); }
.tag.tool { color:var(--accent); border-color:var(--accent); } .tag.err { color:var(--bad); border-color:var(--bad); }
.tag.ok { color:var(--ok); border-color:var(--ok); } .tag.warn { color:var(--warn); border-color:var(--warn); }
pre { background:var(--code); border-radius:6px; padding:8px 10px; overflow-x:auto; white-space:pre-wrap; word-break:break-word; margin:6px 0; font-size:12.5px; }
details > summary { cursor:pointer; color:var(--muted); font-size:13px; margin:4px 0; }
.think pre { background:var(--think); }
.err pre { border-left:3px solid var(--bad); }
.cols { display:grid; grid-template-columns:1fr 1fr; gap:8px; } @media (max-width:760px) { .cols { grid-template-columns:1fr; } }
nav a { color:var(--accent); margin-right:10px; text-decoration:none; font-size:13px; }
"""

LONG = 1500     # results longer than this are folded


def _e(text) -> str:
    return html.escape("" if text is None else str(text))


def _pretty(value) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return value
    try:
        return json.dumps(value, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _block(label: str, text, open_: bool = True, cls: str = "") -> str:
    text = "" if text is None else str(text)
    if not text.strip():
        return ""
    is_open = open_ and len(text) <= LONG
    return (f'<details class="{cls}"{" open" if is_open else ""}><summary>{_e(label)} '
            f'({len(text):,} chars)</summary><pre>{_e(text)}</pre></details>')


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue                          # a line being written right now
    return rows


def _content(msg: dict) -> str:
    c = msg.get("content")
    return c if isinstance(c, str) else "".join(p.get("text", "") for p in (c or []) if isinstance(p, dict))


def _step_html(row: dict) -> str:
    calls = row.get("tool_calls") or []
    names = ", ".join(c.get("name", "?") for c in calls) or "no tool call"
    err = row.get("error")
    tags = [f'<span class="tag tool">{_e(names)}</span>']
    if err:
        tags.append('<span class="tag err">error</span>')
    if row.get("is_final_answer"):
        tags.append('<span class="tag ok">final answer</span>')
    dur = (row.get("end_time") or 0) - (row.get("start_time") or 0)
    parts = [f'<div class="card"><div class="step-h"><b>Step {_e(row.get("agent_step"))}</b>{"".join(tags)}'
             f'<span class="meta">{dur:.0f} s</span></div>']
    for i, g in enumerate(row.get("generations") or []):
        label = "Model output" if i == 0 else f"Model output, parse retry {i}"
        parts.append(_block(f"{label}: reasoning", g.get("think"), open_=False, cls="think"))
        parts.append(_block(f"{label}: visible text", g.get("visible") or g.get("raw_text"), open_=True))
    for c in calls:
        parts.append(_block(f"Tool call: {c.get('name')}", _pretty(c.get("arguments")), open_=True))
    parts.append(_block("Tool result", _pretty(row.get("observations")), open_=True))
    if err:
        parts.append(_block("Error", err, open_=True, cls="err"))
    parts.append("</div>")
    return "".join(parts)


def render(step_log: Path, events_path: Path | None, out: Path, title: str) -> Path:
    rows = [r for r in _read_jsonl(step_log) if r.get("kind") == "step"]
    events = _read_jsonl(events_path) if events_path else []
    if events:
        running = not any(e.get("type") == "done" for e in events)
    else:   # a plain step log: live only if its last row was written in the last two minutes
        last = max((r.get("time") or 0 for r in rows), default=0)
        running = time.time() - float(last) < 120
    head = next((e for e in events if e.get("type") == "start"), {})
    by_agent: dict[str, list[dict]] = {}
    for r in rows:
        by_agent.setdefault(r.get("agent", "?"), []).append(r)
    stage_events = {}
    for e in events:
        stage_events.setdefault(e.get("stage"), []).append(e)
    stages = [e["stage"] for e in events if e.get("type") == "stage_start"] or list(by_agent)

    body = [f"<h1>{_e(title)}</h1>",
            f'<div class="meta">{"running, refreshes every 5 s" if running else "finished"} · '
            f'rendered {time.strftime("%Y-%m-%d %H:%M:%S")} · {len(rows)} steps</div>']
    if head:
        body.append(_block("Sandbox setup", _pretty({k: v for k, v in head.items() if k != "type"}), open_=False))
    body.append("<nav>" + "".join(f'<a href="#{_e(s)}">{_e(s)}</a>' for s in stages) + "</nav>")
    system_shown = False
    for stage in stages:
        evs = stage_events.get(stage, [])
        start = next((e for e in evs if e.get("type") == "stage_start"), {})
        end = next((e for e in evs if e.get("type") == "stage_end"), {})
        status = ('<span class="tag ok">done</span>' if end else '<span class="tag warn">running</span>') if evs else ""
        secs = f'<span class="meta">{end.get("seconds", 0):.0f} s</span>' if end else ""
        body.append(f'<h2 id="{_e(stage)}">{_e(stage)} {status} {secs}</h2>')
        agent_rows = by_agent.get(stage, [])
        stage_input = start.get("input")
        if agent_rows:
            msgs = ((agent_rows[0].get("generations") or [{}])[0].get("prompt") or {}).get("messages") or []
            if msgs and not system_shown:
                body.append(_block("System message (the Initial Prompt)", _content(msgs[0]), open_=False))
                system_shown = True
            if stage_input is None and len(msgs) > 1:
                stage_input = _content(msgs[1])
        body.append(_block("Input to this stage (exactly what the model received)", stage_input, open_=False))
        for r in agent_rows:
            body.append(_step_html(r))
        for e in evs:
            if e.get("type") == "replay_call":
                differs = e.get("recorded") != e.get("replayed")
                badge = '<span class="tag warn">differs from the recorded run</span>' if differs else \
                    '<span class="tag ok">same as recorded</span>'
                body.append(f'<div class="card"><div class="step-h"><b>Replay</b>'
                            f'<span class="tag tool">{_e(e.get("tool"))}</span>{badge}</div>'
                            + _block("Arguments (as recorded)", _pretty(e.get("arguments")))
                            + '<div class="cols"><div>' + _block("Recorded result", _pretty(e.get("recorded")))
                            + '</div><div>' + _block("Replayed now", _pretty(e.get("replayed"))) + "</div></div></div>")
        if end:
            body.append(_block("Stage output (passed to the next stage)", end.get("output"), open_=True))
            body.append(_block("design.json after this stage", end.get("design_json"), open_=False))
            if end.get("error"):
                body.append(_block("Stage error", end.get("error"), open_=True, cls="err"))
    refresh = '<meta http-equiv="refresh" content="5">' if running else ""
    out.write_text(f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
                   f'<meta name="viewport" content="width=device-width, initial-scale=1">{refresh}'
                   f"<title>Stage sandbox</title><style>{CSS}</style></head><body><main>"
                   + "".join(body) + "</main></body></html>", encoding="utf-8")
    return out


def render_target(target: Path) -> Path:
    if target.is_dir():
        logs = sorted(target.glob("step_log_attempt*.jsonl"))
        step_log = logs[-1] if logs else target / "step_log_attempt1.jsonl"
        return render(step_log, target / "events.jsonl", target / "report.html", f"Stage sandbox · {target.name}")
    return render(target, None, target.with_suffix(".html"), f"Step log · {target.parent.name} · {target.stem}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", type=Path, help="a sandbox folder, or a step_log_attempt*.jsonl")
    print(render_target(ap.parse_args().target))

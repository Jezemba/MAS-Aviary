"""Extract the validation-1 records the B372 tests replay (V1, V5, V9, V11). Run on ws8, read-only on the run data.

    python tests/fixtures/v1a_extract_validation1_records.py /data/Jessica/final_val tests/fixtures/v1a_validation1_records.json

Sources (2026-11-18, pinned 140865a; FINAL_RERUN_VALIDATION1_2026-11-18.md):
  runs/val_*                                    ws8 runs (O_GR, N_GR, N_IF)
  analysis_check/copies/ws5/runs/val_*          S_GR, S_SP
  analysis_check/copies/ws6/runs/val_*          O_SP, O_IF
  ws3_copy/runs/val_seq_iter                    S_IF
Large strings (meshes) are replaced by "<N chars>"; agent role texts are dropped. Nothing else is changed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOTS = {"ws8": "runs", "ws5": "analysis_check/copies/ws5/runs", "ws6": "analysis_check/copies/ws6/runs",
         "ws3": "ws3_copy/runs"}
BIG = 3000
KEEP_TOOLS = {"open_cpacs", "get_high_level_parameters", "set_high_level_parameters", "export_cpacs", "close_cpacs",
              "set_aircraft_parameters", "run_simulation", "get_results", "estimate_mass",
              "set_inputs", "get_cruise_point", "run_cycle"}
KB_LINKS = {"val_seq_graph/repeat_000", "val_net_graph/repeat_000", "val_net_graph/repeat_001", "val_orch_graph/repeat_000",
            "val_orch_iter/repeat_001"}


def _shrink(value):
    if isinstance(value, str):
        return value if len(value) <= BIG else f"<{len(value)} chars>"
    if isinstance(value, dict):
        return {k: _shrink(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shrink(v) for v in value]
    return value


def _jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _links(base: Path):
    for machine, rel in ROOTS.items():
        for kb in sorted((base / rel).glob("val_*/MAS-Aviary/logs/stat_results/val_*/repeat_*/*/knowledge_base.jsonl")):
            yield machine, kb.parent


def _kb(link: Path) -> list[dict]:
    rows = _jsonl(link / "knowledge_base.jsonl")
    for r in rows:
        r.pop("role", None)
    return rows


def _calls(link: Path) -> list[dict]:
    path = link / "tool_calls_attempt1.jsonl"
    return [_shrink(c) for c in _jsonl(path)] if path.is_file() else []


def main(base: str, out: str) -> None:
    base_p = Path(base)
    doc: dict = {"source": base, "missions": [], "links": {}}
    for machine, link in _links(base_p):
        name = f"{link.parent.parent.name}/{link.parent.name}"           # e.g. val_seq_graph/repeat_000
        kb = _kb(link)
        # V1: every run_simulation the knowledge base recorded as a success, exactly as recorded (capped outputs).
        for r in kb:
            if r.get("tool") == "run_simulation" and r.get("status") == "success":
                doc["missions"].append({"link": name, "machine": machine, "record": r})
        doc["links"][name] = {"machine": machine}
        if name in KB_LINKS:
            doc["links"][name]["kb"] = [
                {k: r.get(k) for k in ("seq", "agent", "tool", "status", "design_fingerprint", "inputs", "outputs", "note")}
                for r in kb]
    # Full server replies for the replays: S_GR link 1 (V1 sizes, V9 SPAN), N_GR link 1 (V5 no-op morph),
    # O_IF link 2 (V5 re-open), all tool calls in order as the tool_calls log has them.
    for name in ("val_seq_graph/repeat_000", "val_net_graph/repeat_000", "val_orch_iter/repeat_001",
                 "val_seq_staged/repeat_000", "val_seq_staged/repeat_001", "val_seq_iter/repeat_000",
                 "val_seq_iter/repeat_001"):
        for machine, link in _links(base_p):
            if f"{link.parent.parent.name}/{link.parent.name}" == name:
                # Only the calls the replays use (geometry, the mission set and flight, mass, engine sizing).
                calls = [c for c in _calls(link) if c.get("tool") in KEEP_TOOLS]
                doc["links"][name]["tool_calls"] = calls
                for extra in ("design_flown.json", "carry_over.json", "design.json"):
                    p = link / extra
                    if p.is_file():
                        doc["links"][name][extra] = json.loads(p.read_text())
    Path(out).write_text(json.dumps(doc, default=str))
    print(f"{len(doc['missions'])} recorded run_simulation successes; links: {sorted(doc['links'])}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

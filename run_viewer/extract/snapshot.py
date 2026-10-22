"""Copy the run artifacts that live OUTSIDE the run folder into the viewer's work folder.

The aviary recorder (``aviary-mcp/aviary_<session>_out/problem_history.db``) and the SU2
session folders (``surface_flow.csv`` etc.) are not part of a run's output and get cleaned
up to save disk. This copies the small files the viewer needs, read-only on the sources:

    <out>/<run>/<repeat>/<combination>/aviary/<session_id>.db
    <out>/<run>/<repeat>/<combination>/su2/<kb_seq>_<workdir_name>/{surface_flow.csv, ...}

Re-running is cheap: files already copied with the same size are skipped.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil

SU2_FILES = ("surface_flow.csv", "surface_flow.vtu", "history.csv", "config.cfg")


def aviary_sessions(link_dir: str) -> list[str]:
    """Aviary session ids named by the link's design.json (and its earlier attempts)."""
    sids = []
    for path in sorted(glob.glob(os.path.join(link_dir, "design*.json"))):
        try:
            sid = (json.load(open(path)).get("sessions") or {}).get("aviary")
        except (OSError, ValueError):
            continue
        if sid and sid not in sids:
            sids.append(sid)
    return sids


def su2_solves(link_dir: str) -> list[tuple[int, str]]:
    """(kb seq, workdir) for every successful run_su2_solver call in the knowledge base."""
    kb = os.path.join(link_dir, "knowledge_base.jsonl")
    solves = []
    if not os.path.exists(kb):
        return solves
    for line in open(kb):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("tool") != "run_su2_solver" or row.get("status") != "success":
            continue
        config = (row.get("outputs") or {}).get("config_used")
        if config:
            solves.append((row["seq"], os.path.dirname(config)))
    return solves


def _copy(src: str, dst: str) -> bool:
    if not os.path.isfile(src):
        return False
    if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src):
        return True
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return True


def snapshot_link(link_dir: str, out_dir: str, aviary_dir: str, su2_root: str | None) -> dict:
    """Copy one link's external artifacts; returns what was found."""
    found = {"aviary": [], "su2": [], "su2_missing": []}
    for sid in aviary_sessions(link_dir):
        src = os.path.join(aviary_dir, f"aviary_{sid}_out", "problem_history.db")
        if _copy(src, os.path.join(out_dir, "aviary", f"{sid}.db")):
            found["aviary"].append(sid)
    for seq, workdir in su2_solves(link_dir):
        # A run on another machine recorded its own absolute path; su2_root re-roots it.
        local = os.path.join(su2_root, os.path.basename(workdir)) if su2_root else workdir
        name = f"{seq:04d}_{os.path.basename(workdir)}"
        copied = [f for f in SU2_FILES
                  if _copy(os.path.join(local, f), os.path.join(out_dir, "su2", name, f))]
        (found["su2"] if "surface_flow.csv" in copied else found["su2_missing"]).append(name)
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", required=True, help="output dir of a run (holds repeat_NNN/)")
    ap.add_argument("--out", required=True, help="snapshot root; the run's name is appended")
    ap.add_argument("--aviary-dir", required=True, help="folder holding aviary_<sid>_out/")
    ap.add_argument("--su2-root", default=None,
                    help="re-root SU2 workdirs here (for runs copied from another machine)")
    ap.add_argument("--name", default=None, help="snapshot folder name (default: run dir name)")
    args = ap.parse_args()

    run_name = args.name or os.path.basename(os.path.normpath(args.run_dir))
    summary = {}
    for link_dir in sorted(glob.glob(os.path.join(args.run_dir, "repeat_*", "*", ""))):
        repeat, combo = link_dir.rstrip("/").split("/")[-2:]
        out = os.path.join(args.out, run_name, repeat, combo)
        summary[f"{repeat}/{combo}"] = snapshot_link(link_dir, out, args.aviary_dir, args.su2_root)
    os.makedirs(os.path.join(args.out, run_name), exist_ok=True)
    with open(os.path.join(args.out, run_name, "snapshot_index.json"), "w") as f:
        json.dump(summary, f, indent=1)
    n = len(summary)
    print(f"{run_name}: {n} links, aviary db {sum(bool(v['aviary']) for v in summary.values())}/{n}, "
          f"su2 surface {sum(bool(v['su2']) for v in summary.values())}/{n}")


if __name__ == "__main__":
    main()

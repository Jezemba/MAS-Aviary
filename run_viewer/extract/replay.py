"""Re-run the SU2 solves and aviary missions whose output files were not saved, offline.

For each link that ran SU2 but has no saved surface files, this replays the link's LAST successful
solve from the knowledge base: the same generate_volume_mesh inputs on the geometry of that moment,
the same create_su2_session / configure_from_cpacs / update_config_entries inputs, the same solver
call. For a link whose aviary recorder is missing it re-flies the mission from the inputs recorded
by get_results. The MCP packages are imported as libraries in their own envs (helpers/replay_*.py):
no MCP server is contacted, so a run in progress is not touched. Everything runs at nice 19 with a
capped SU2 rank count, writes only under --work, and never overwrites an original snapshot.

A solve is only replayed when the geometry of that moment is on disk:
  * the CPACS the tigl session opened, if nothing modified that session before the mesh, or
  * the link's geometry_end.xml, if no geometry change came after the mesh.
Otherwise it is reported as skipped, with the reason.

    python -m run_viewer.extract.replay --catalog runs.json --work ~/Jessica/run_viewer_work [--dry-run]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import time

from run_viewer.extract.build import _load_json, _load_jsonl
from run_viewer.extract.snapshot import SU2_FILES, has_surface

# tigl tools that do not change the geometry of their session
TIGL_READ_ONLY = {"open_cpacs", "close_cpacs", "generate_volume_mesh", "export_cpacs", "get_session_info"}
TIGL_READ_PREFIXES = ("get_", "list_", "validate_", "export_", "compute_", "sample_")


def _modifies_geometry(row: dict) -> bool:
    tool = row.get("tool") or ""
    return (row.get("server") == "tigl" and row.get("status") == "success" and tool not in TIGL_READ_ONLY
            and not tool.startswith(TIGL_READ_PREFIXES))


def map_path(path: str | None, path_map: dict[str, str], fixtures_dir: str | None) -> str | None:
    """A path recorded on the run's machine, as it exists here (or None)."""
    if not path:
        return None
    for src, dst in path_map.items():
        if path.startswith(src):
            path = dst + path[len(src):]
            break
    if os.path.exists(path):
        return path
    if fixtures_dir and "/tests/fixtures/" in path:
        local = os.path.join(fixtures_dir, os.path.basename(path))
        if os.path.exists(local):
            return local
    return None


def plan_su2(link_dir: str, path_map: dict[str, str], fixtures_dir: str | None) -> dict:
    """What to replay for the link's last successful solve, or {'skip': reason}."""
    kb = _load_jsonl(os.path.join(link_dir, "knowledge_base.jsonl"))
    solves = [r for r in kb if r.get("tool") == "run_su2_solver" and r.get("status") == "success"]
    if not solves:
        return {"skip": "no successful SU2 solve in this link"}
    solve = solves[-1]
    sid = (solve.get("inputs") or {}).get("session_id")
    before = [r for r in kb if r["seq"] < solve["seq"]]
    create = next((r for r in before if r.get("tool") == "create_su2_session" and r.get("status") == "success"
                   and (r.get("outputs") or {}).get("session_id") == sid), None)
    if create is None:
        return {"skip": f"no create_su2_session recorded for SU2 session {sid}"}
    mesh_feeds = [r for r in before if r["seq"] >= create["seq"] and r.get("tool") == "set_mesh"
                  and (r.get("inputs") or {}).get("session_id") == sid and r.get("status") == "success"]
    feed = mesh_feeds[-1] if mesh_feeds else create
    gvm = next((r for r in reversed(before) if r["seq"] < feed["seq"] and r.get("tool") == "generate_volume_mesh"
                and r.get("status") == "success"), None)
    if gvm is None:
        return {"skip": "no generate_volume_mesh recorded before the SU2 session's mesh"}
    tsid = (gvm.get("inputs") or {}).get("session_id")
    opened = next((r for r in before if r.get("tool") == "open_cpacs" and r.get("status") == "success"
                   and (r.get("outputs") or {}).get("session_id") == tsid), None)
    changed_before = [r for r in before if _modifies_geometry(r) and (opened is None or r["seq"] > opened["seq"])
                      and r["seq"] < gvm["seq"] and (r.get("inputs") or {}).get("session_id") == tsid]
    changed_after = [r for r in kb if _modifies_geometry(r) and r["seq"] > gvm["seq"]]
    geometry, source = None, None
    src_path = map_path(((opened or {}).get("inputs") or {}).get("source"), path_map, fixtures_dir)
    if opened and src_path and not changed_before:
        geometry, source = src_path, "the CPACS the tigl session opened (unchanged before the mesh)"
    elif os.path.exists(os.path.join(link_dir, "geometry_end.xml")) and not changed_after:
        geometry, source = os.path.join(link_dir, "geometry_end.xml"), "geometry_end.xml (no geometry change after the mesh)"
    if geometry is None:
        return {"skip": "the geometry at the time of the mesh was not saved", "solve_seq": solve["seq"]}

    def fix_cpacs(kwargs: dict) -> dict:
        kw = {k: v for k, v in kwargs.items() if k not in ("session_id", "initial_mesh")}
        if "cpacs_file_path" in kw:
            kw["cpacs_file_path"] = map_path(kw["cpacs_file_path"], path_map, fixtures_dir) or geometry
        return kw

    configure = [fix_cpacs(r.get("inputs") or {}) for r in before if r["seq"] > create["seq"]
                 and r.get("tool") == "configure_from_cpacs" and r.get("status") == "success"
                 and (r.get("inputs") or {}).get("session_id") == sid]
    updates = [{k: v for k, v in (r.get("inputs") or {}).items() if k != "session_id"} for r in before
               if r["seq"] > create["seq"] and r.get("tool") == "update_config_entries" and r.get("status") == "success"
               and (r.get("inputs") or {}).get("session_id") == sid]
    coeffs = (solve.get("outputs") or {}).get("final_coefficients") or {}
    return {
        "solve_seq": solve["seq"], "geometry": geometry, "geometry_source": source,
        "mesh_params": {k: v for k, v in (gvm.get("inputs") or {}).items() if k != "session_id"},
        "create": fix_cpacs(create.get("inputs") or {}), "configure": configure, "updates": updates,
        "solver": {k: v for k, v in (solve.get("inputs") or {}).items() if k != "session_id"},
        "original": {"CL": coeffs.get("CL"), "CD": coeffs.get("CD"),
                     "mpi_ranks": (solve.get("outputs") or {}).get("mpi_ranks")},
    }


def plan_aviary(link_dir: str) -> dict:
    """Mission inputs for the link's last simulation, as get_results recorded them."""
    kb = _load_jsonl(os.path.join(link_dir, "knowledge_base.jsonl"))
    sims = [r for r in kb if r.get("tool") == "run_simulation" and r.get("status") == "success"]
    if not sims:
        return {"skip": "no successful run_simulation in this link"}
    last = sims[-1]
    res = next((r for r in kb if r["seq"] > last["seq"] and r.get("tool") == "get_results"
                and r.get("status") == "success"
                and isinstance((r.get("outputs") or {}).get("design_parameters"), dict)), None)
    if res is None:
        return {"skip": "no get_results after the last simulation, so its exact inputs were not recorded"}
    dp = res["outputs"]["design_parameters"]
    summ = (last.get("outputs") or {}).get("summary") or {}
    return {"sim_seq": last["seq"], "aircraft_params": dp.get("aircraft_params"), "mission_config": dp.get("mission_config"),
            "original": {"fuel_burned_kg": summ.get("fuel_burned_kg"), "gtow_kg": summ.get("gtow_kg"),
                         "converged": (last.get("outputs") or {}).get("converged")}}


def _nice(cmd: list[str]) -> list[str]:
    return ["nice", "-n", "19"] + cmd


def replay_su2(plan: dict, out_dir: str, work: str, cfg: dict, ranks: int, log) -> dict:
    tag = os.path.basename(out_dir.rstrip("/"))
    scratch = os.path.join(work, "replay", "scratch", tag)
    os.makedirs(scratch, exist_ok=True)
    mesh = os.path.join(scratch, "mesh.su2")
    t0 = time.time()
    if not os.path.exists(mesh):
        proc = subprocess.run(_nice([cfg["tigl_python"], "-I", os.path.join(cfg["helpers"], "replay_mesh.py"),
                                     plan["geometry"], json.dumps(plan["mesh_params"]), mesh]),
                              capture_output=True, text=True, timeout=7200)
        if proc.returncode != 0:
            return {"status": "mesh failed", "error": proc.stderr[-1500:]}
    t_mesh = time.time() - t0
    plan_path = os.path.join(scratch, "plan.json")
    with open(plan_path, "w") as f:
        json.dump({"mesh": mesh, "create": plan["create"], "configure": plan["configure"],
                   "updates": plan["updates"], "solver": plan["solver"]}, f)
    tmpdir = os.path.join(scratch, "tmp")
    os.makedirs(tmpdir, exist_ok=True)
    # same PATH head as the su2-mcp server, so SU2_CFD and its mpirun are found
    env = {**os.environ, "TMPDIR": tmpdir, "SU2_MPI_RANKS": str(ranks),
           "PATH": f"{cfg['su2_bin']}:{os.environ.get('PATH', '')}"}
    out_json = os.path.join(scratch, "solve.json")
    proc = subprocess.run(_nice([cfg["su2_python"], "-I", os.path.join(cfg["helpers"], "replay_su2.py"),
                                 plan_path, out_json]), capture_output=True, text=True, env=env, timeout=4 * 3600)
    if proc.returncode != 0:
        return {"status": "solve failed", "error": proc.stderr[-1500:]}
    res = json.load(open(out_json))
    workdir = res["workdir"]
    os.makedirs(out_dir, exist_ok=True)
    for name in SU2_FILES:
        if os.path.exists(os.path.join(workdir, name)):
            shutil.copy2(os.path.join(workdir, name), os.path.join(out_dir, name))
    coeffs = res["solve"].get("final_coefficients") or {}
    note = {"replayed": True, "replayed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "solve_seq": plan["solve_seq"], "geometry": plan["geometry"], "geometry_source": plan["geometry_source"],
            "mesh_params": plan["mesh_params"], "original": plan["original"],
            "replay": {"CL": coeffs.get("CL"), "CD": coeffs.get("CD"), "mpi_ranks": res["solve"].get("mpi_ranks"),
                       "exit_code": res["solve"].get("exit_code"), "success": res["solve"].get("success")},
            "mesh_seconds": round(t_mesh, 1), "total_seconds": round(time.time() - t0, 1), "workdir": workdir}
    with open(os.path.join(out_dir, "replay.json"), "w") as f:
        json.dump(note, f, indent=1)
    log(f"   CL {plan['original']['CL']} -> {coeffs.get('CL')} | CD {plan['original']['CD']} -> {coeffs.get('CD')}")
    return {"status": "ok" if has_surface(out_dir) else "solve ran but wrote no surface file", **note["replay"]}


def replay_aviary(plan: dict, snap_dir: str, sid: str, work: str, cfg: dict) -> dict:
    scratch = os.path.join(work, "replay", "aviary", sid)
    os.makedirs(scratch, exist_ok=True)
    inputs = os.path.join(scratch, "inputs.json")
    with open(inputs, "w") as f:
        json.dump({"aircraft_params": plan["aircraft_params"], "mission_config": plan["mission_config"]}, f)
    out_json = os.path.join(scratch, "run.json")
    proc = subprocess.run(_nice([cfg["aviary_python"], os.path.join(cfg["helpers"], "replay_aviary.py"),
                                 cfg["aviary_mcp"], inputs, f"replay_{sid}", out_json]),
                          capture_output=True, text=True, cwd=scratch, timeout=3600)
    if proc.returncode != 0:
        return {"status": "aviary failed", "error": proc.stderr[-1500:]}
    db = os.path.join(scratch, f"aviary_replay_{sid}_out", "problem_history.db")
    if not os.path.exists(db):
        return {"status": "aviary ran but wrote no recorder"}
    run = json.load(open(out_json))
    os.makedirs(os.path.join(snap_dir, "aviary"), exist_ok=True)
    shutil.copy2(db, os.path.join(snap_dir, "aviary", f"{sid}.db"))
    note = {"replayed": True, "sim_seq": plan["sim_seq"], "original": plan["original"],
            "replay": {"fuel_burned_kg": (run.get("summary") or {}).get("fuel_burned_kg"),
                       "gtow_kg": (run.get("summary") or {}).get("gtow_kg"), "converged": run.get("converged")}}
    with open(os.path.join(snap_dir, "aviary", f"{sid}.replay.json"), "w") as f:
        json.dump(note, f, indent=1)
    return {"status": "ok", **note["replay"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--ranks", type=int, default=8, help="SU2 MPI ranks (kept low to leave the CPU to live runs)")
    ap.add_argument("--path-map", default="{}", help='JSON {"recorded prefix": "local prefix"}')
    ap.add_argument("--su2-python", required=True)
    ap.add_argument("--aviary-mcp", required=True)
    ap.add_argument("--su2-bin", required=True, help="folder holding SU2_CFD (the su2-mcp server's PATH head)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=None, help="replay only links whose '<run_id>/<repeat>' contains this")
    args = ap.parse_args()
    cat = json.load(open(args.catalog))
    cfg = {"tigl_python": cat["tigl_python"], "aviary_python": cat["aviary_python"], "su2_python": args.su2_python,
           "aviary_mcp": args.aviary_mcp, "su2_bin": args.su2_bin, "helpers": os.path.join(os.path.dirname(__file__), "helpers")}
    path_map = json.loads(args.path_map)
    log_path = os.path.join(args.work, "logs", "replay.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logf = open(log_path, "a")

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    report = []
    for r in cat["runs"]:
        if r.get("live") or not r.get("snapshot"):
            continue  # never replay a run in progress
        for link_dir in sorted(glob.glob(os.path.join(r["run_dir"], "repeat_*", r["combination"]))):
            repeat = os.path.basename(os.path.dirname(link_dir))
            snap = os.path.join(r["snapshot"], repeat, r["combination"])
            name = f"{r['run_id']}/{repeat}"
            if args.only and args.only not in name:
                continue
            has_su2 = any(has_surface(d) for d in glob.glob(os.path.join(snap, "su2", "*")))
            if not has_su2:
                plan = plan_su2(link_dir, path_map, cat.get("fixtures_dir"))
                if "skip" in plan:
                    if plan["skip"] != "no successful SU2 solve in this link":
                        report.append({"link": name, "kind": "su2", "status": "skipped", "why": plan["skip"]})
                        log(f"{name} su2 SKIP: {plan['skip']}")
                else:
                    log(f"{name} su2 replay seq {plan['solve_seq']} ({plan['geometry_source']})")
                    out = {"status": "planned"} if args.dry_run else replay_su2(
                        plan, os.path.join(snap, "su2", f"{plan['solve_seq']:04d}_replayed"), args.work, cfg, args.ranks, log)
                    report.append({"link": name, "kind": "su2", **out})
                    log(f"{name} su2 -> {out['status']}")
            sid = ((_load_json(os.path.join(link_dir, "design.json")) or {}).get("sessions") or {}).get("aviary")
            if sid and not os.path.exists(os.path.join(snap, "aviary", f"{sid}.db")):
                plan = plan_aviary(link_dir)
                if "skip" in plan:
                    report.append({"link": name, "kind": "aviary", "status": "skipped", "why": plan["skip"]})
                    log(f"{name} aviary SKIP: {plan['skip']}")
                else:
                    log(f"{name} aviary replay of sim seq {plan['sim_seq']}")
                    out = {"status": "planned"} if args.dry_run else replay_aviary(plan, snap, sid, args.work, cfg)
                    report.append({"link": name, "kind": "aviary", **out})
                    log(f"{name} aviary -> {out['status']}")
    with open(os.path.join(args.work, "logs", f"replay_report{'_dry' if args.dry_run else ''}.json"), "w") as f:
        json.dump(report, f, indent=1)
    log(f"done: {sum(x['status'] in ('ok', 'planned') for x in report)} ok/planned, "
        f"{sum(x['status'] == 'skipped' for x in report)} skipped, "
        f"{sum(x['status'] not in ('ok', 'planned', 'skipped') for x in report)} failed")


if __name__ == "__main__":
    main()

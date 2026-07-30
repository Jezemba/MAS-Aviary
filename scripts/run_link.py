"""Run ONE full-pipeline MDO evaluation of a given aircraft design — the real thing:
geometry morph -> volume mesh -> SU2 (Euler + physics skin-friction coupling) -> mass
(flops/aluminum) -> pycycle -> aviary mission, all driven by the SHARED canonical config.
Returns structured JSON so a live subagent can optimize across a chain of links.

Usage:
  MCP_STATE_FILE=... MCP_LOG_FILE=... \
  python scripts/run_link.py --design '{"Aircraft.Wing.ASPECT_RATIO":11,...}' --link 0 --combo <name>

Design keys (any subset; missing ones keep the anchor): Aircraft.Wing.ASPECT_RATIO, .AREA,
.SWEEP, .TAPER_RATIO, Aircraft.Fuselage.LENGTH/MAX_HEIGHT/MAX_WIDTH, Aircraft.Engine.SCALE_FACTOR.

Prints a JSON object: {ok, fuel_burned_kg, gtow_kg, wing_mass_kg, cruise_cd_avg, coupled,
drag_factor, end_design, notes}. Never raises — errors come back in `notes` with ok=false.
"""
import argparse
import contextlib
import io
import json
import math
import os
import sys
import time

sys.path.insert(0, ".")
os.environ.setdefault("AVION_ENFORCE_COUPLING", "1")
from src.config.loader import load_config                       # noqa: E402
from src.config.canonical import load_canonical, render_snippets  # noqa: E402
from scripts.stat_batch_runner import _load_mcp_tools, generate_random_params, PARAM_RANGES  # noqa: E402

CPACS = "/home/aipexws3/Jessica/Avion/mass-mcp/tests/fixtures/D150_simple.xml"
LOGF = os.environ.get("MCP_LOG_FILE", "logs/live_agent_chain.jsonl")


def _log(link, combo, step, **kw):
    rec = {"t": round(time.time(), 1), "combo": combo, "link": link, "step": step, **kw}
    try:
        os.makedirs(os.path.dirname(LOGF) or ".", exist_ok=True)
        with open(LOGF, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass


def P(r):
    return r if isinstance(r, dict) else (json.loads(r) if isinstance(r, str) and r.strip()[:1] in "{[" else {"raw": r})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", default="{}")
    ap.add_argument("--link", type=int, default=0)
    ap.add_argument("--combo", default="live")
    args = ap.parse_args()
    combo, link = args.combo, args.link
    notes = []

    canon = load_canonical()
    su2_cfg = json.loads(render_snippets()["SU2_CONFIG"])
    mis = canon["mission"]; mass_c = canon.get("mass", {}); prop = canon.get("propulsion", {})

    anchor = {k: v for k, v in generate_random_params(42).items() if k in PARAM_RANGES}
    design = dict(anchor)
    try:
        design.update({k: float(v) for k, v in json.loads(args.design).items() if k in PARAM_RANGES})
    except Exception as e:
        notes.append(f"bad --design ({e}); used anchor")

    AR = design["Aircraft.Wing.ASPECT_RATIO"]; AREA = design["Aircraft.Wing.AREA"]
    TAPER = design["Aircraft.Wing.TAPER_RATIO"]; SWEEP = min(max(design["Aircraft.Wing.SWEEP"], 20.0), 35.0)
    span = round(math.sqrt(AR * AREA), 3)
    root = round(2 * AREA / ((1 + TAPER) * span), 3); tip = round(root * TAPER, 3)
    _log(link, combo, "start", design={k.split(".")[-1]: round(v, 3) for k, v in design.items()},
         morph={"span": span, "root_chord": root, "tip_chord": tip, "sweep": SWEEP})

    with contextlib.redirect_stderr(io.StringIO()):
        tm = _load_mcp_tools(load_config("config/mdo_f25_run_claude.yaml"))

    def call(name, **a):
        with contextlib.redirect_stderr(io.StringIO()):
            return P(tm[name].forward(**a))

    out = {"ok": False, "combo": combo, "link": link}
    try:
        # GEOMETRY — morph (span+chords+sweep) so it actually rebuilds
        g = call("open_cpacs", source_type="path", source=CPACS); gid = g.get("session_id")
        m = call("set_high_level_parameters", session_id=gid, component_uid="Wing1",
                 updates={"span": span, "root_chord": root, "tip_chord": tip, "sweep": SWEEP})
        gm = m.get("geometry_morph") or {}
        ws = call("get_wing_summary", session_id=gid, wing_uid="Wing1")
        call("get_fuselage_summary", session_id=gid, fuselage_uid="Fuselage1")
        _log(link, combo, "geometry", morphed=bool(gm.get("rebuilt")), span=ws.get("span"), ref_area=ws.get("reference_area"))
        # Persist the MORPHED geometry to a CPACS file so mass-mcp (now tigl3-capable)
        # computes structural mass on the ACTUAL design, not the baseline. Must run
        # while the session is open (before close_cpacs). The path MUST be ABSOLUTE:
        # each MCP server has its own cwd, so a relative path lands in the tigl server's
        # dir and mass-mcp can't find it (silently falls back to baseline geometry).
        morphed_cpacs = os.path.abspath(os.path.join(os.path.dirname(LOGF) or ".", f"morphed_{combo}_{link}.xml"))
        ex = call("export_cpacs", session_id=gid, output_path=morphed_cpacs)
        _exp_path = ex.get("cpacs_file_path")
        # Guard against the silent-fallback trap: only treat the export as usable if the
        # file ACTUALLY exists on disk from our vantage point. A missing file here means
        # mass would read baseline while the log falsely claimed "morphed".
        if ex.get("status") == "success" and _exp_path and os.path.isfile(_exp_path):
            mass_cpacs = _exp_path
        else:
            mass_cpacs = CPACS
            notes.append(f"export_cpacs unusable (status={ex.get('status')}, path={_exp_path}, "
                         f"exists={bool(_exp_path) and os.path.isfile(str(_exp_path))}); mass used baseline CPACS")
        call("generate_volume_mesh", session_id=gid, component_uid="Wing1",
             far_field_distance=canon["geometry"]["far_field_distance"], boundary_layer_enabled=False)
        call("close_cpacs", session_id=gid)
        # SU2 — full canonical config + geometry-derived REF
        s = call("create_su2_session", mesh_file_name="mesh.su2", base_name=f"f25_{combo}"); ssid = s.get("session_id")
        call("set_mesh", session_id=ssid, mesh_base64={"ref": "generate_volume_mesh__mesh_base64"}, mesh_file_name="mesh.su2", update_config=True)
        cfg = dict(su2_cfg); mqc = ws.get("mac_quarter_chord") or {}
        cfg.update({"REF_AREA": round(ws.get("reference_area") or 60.0, 3), "REF_LENGTH": round(ws.get("mac_length") or 4.0, 3),
                    "REF_ORIGIN_MOMENT_X": round(mqc.get("x", 15.0), 3), "REF_ORIGIN_MOMENT_Y": 0.0, "REF_ORIGIN_MOMENT_Z": 0.0})
        call("update_config_entries", session_id=ssid, updates=cfg)
        _log(link, combo, "su2_solving")
        rs = call("run_su2_solver", session_id=ssid, solver="SU2_CFD", max_runtime_seconds=300)
        call("read_history_csv", session_id=ssid, relative_path="history.csv", max_rows=2000, skip_rows=0)
        _log(link, combo, "su2_done", exit_code=rs.get("exit_code"), runtime=rs.get("runtime_seconds"))
        # MASS — canonical flops/aluminum, on the MORPHED geometry (export_cpacs above +
        # tigl3-capable mass-mcp runtime), so structural mass is now geometry-COUPLED.
        mmr = call("estimate_mass", cpacs_file_path=mass_cpacs, wing_mass_method=mass_c.get("wing_mass_method", "flops"),
                   material=mass_c.get("material", "aluminum"), aviary_mass_method="FLOPS", design_load_factor=mass_c.get("design_load_factor", 2.5))
        _mb = mmr.get("mass_breakdown") or {}
        _oem = (_mb.get("mOEM_kg") if isinstance(_mb, dict) else None)
        _wing = ((_mb.get("components") or {}).get("mWing_kg") if isinstance(_mb, dict) else None)
        _log(link, combo, "mass", status=mmr.get("status"), oem_kg=_oem, wing_kg=_wing,
             geom=("morphed" if mass_cpacs != CPACS else "baseline-fallback"))
        if mmr.get("status") != "success":
            notes.append(f"mass-mcp non-success: {mmr.get('error_message')}")
        # PYCYCLE — canonical design point
        c = call("create_cycle_model", cycle_type="turbofan", mode="design"); csid = c.get("session_id")
        call("set_inputs", session_id=csid, values={"fc.alt": prop.get("design_altitude_ft", 33000), "fc.MN": prop.get("design_mach", 0.78),
                                                     "T4_MAX": prop.get("T4_MAX_degR", 2857), "splitter.BPR": prop.get("BPR", 5.105)})
        rcr = call("run_cycle", session_id=csid, use_driver=False, outputs_of_interest=["perf.TSFC", "perf.Fn"])
        _log(link, combo, "pycycle", converged=(rcr.get("converged") or rcr.get("success")))
        # AVIARY — canonical mission + physics coupling (enforced)
        a = call("create_session", initial_parameters={}); asid = a.get("session_id")
        _log(link, combo, "aviary_solving")
        call("configure_mission", session_id=asid, range_nmi=mis["range_nmi"], num_passengers=mis["num_passengers"],
             cruise_mach=mis["cruise_mach"], cruise_altitude_ft=mis["cruise_altitude_ft"], optimizer_max_iter=mis["optimizer_max_iter"])
        sap = call("set_aircraft_parameters", session_id=asid, parameters=design)
        if sap.get("error_code") == "UNCOUPLED_MISSION":
            out["notes"] = ["UNCOUPLED_MISSION — aero did not reach aviary"] + notes
            _log(link, combo, "result", **out); print(json.dumps(out, default=str)); return
        inj = [p["name"].split(".")[-1] for p in (sap.get("applied") or []) if "DRAG" in p["name"] or "LIFT" in p["name"]]
        drag = next((p["new_value"] for p in (sap.get("applied") or []) if "DRAG_COEFF_FACTOR" in p["name"]), None)
        rsim = call("run_simulation", session_id=asid, timeout_seconds=120)
        gr = call("get_results", session_id=asid)
        summ = rsim.get("summary") or {}
        ap2 = (gr.get("design_parameters") or {}).get("aircraft_params") or {}
        out.update({"ok": bool(summ.get("fuel_burned_kg")), "fuel_burned_kg": summ.get("fuel_burned_kg"),
                    "gtow_kg": summ.get("gtow_kg"), "wing_mass_kg": summ.get("wing_mass_kg"),
                    "cruise_cd_avg": gr.get("cruise_cd_avg"), "coupled": bool(inj), "drag_factor": drag,
                    "injected": inj, "end_design": {k: ap2.get(k) for k in PARAM_RANGES if ap2.get(k) is not None},
                    "notes": notes})
    except Exception as e:
        out["notes"] = notes + [f"pipeline error: {type(e).__name__}: {e}"]

    _log(link, combo, "result", ok=out.get("ok"), fuel=out.get("fuel_burned_kg"),
         cruise_cd=out.get("cruise_cd_avg"), coupled=out.get("coupled"), drag_factor=out.get("drag_factor"),
         wing_mass_kg=out.get("wing_mass_kg"), gtow_kg=out.get("gtow_kg"))
    print(json.dumps(out, default=str))


if __name__ == "__main__":
    main()

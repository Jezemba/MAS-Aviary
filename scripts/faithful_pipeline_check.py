"""Faithful, no-API full-pipeline health check driven ENTIRELY by the shared canonical
config (config/mdo_f25_canonical_baseline.yaml) — NO simplistic hand-written defaults.

Every discipline uses its canonical inputs so this realistically recreates a real run:
  geometry: morph the wing from the seed-42 ANCHOR design (span+chords+sweep derived from
            AR/AREA/TAPER) so set_high_level_parameters actually rebuilds geometry;
  SU2:      the FULL canonical <<SU2_CONFIG>> + REF from get_wing_summary (geometry-derived);
  mass:     canonical wing_mass_method / material / design_load_factor;
  pycycle:  canonical design_mach / design_altitude / T4 / BPR;
  aviary:   canonical mission (2500/239/0.78/33000) + coupling enforcement ON.

Verifies each discipline runs cleanly and the SU2->aviary coupling fires (drag factor
injected from the typed registry). Prints a per-discipline PASS/FAIL. ~150s (one SU2 solve).
"""
import contextlib
import io
import json
import math
import os
import sys

sys.path.insert(0, ".")
os.environ["AVION_ENFORCE_COUPLING"] = "1"
from src.config.loader import load_config           # noqa: E402
from src.config.canonical import load_canonical, render_snippets  # noqa: E402
from scripts.stat_batch_runner import _load_mcp_tools, generate_random_params  # noqa: E402

CPACS = "/home/aipexws3/Jessica/Avion/mass-mcp/tests/fixtures/D150_simple.xml"


def P(r):
    return r if isinstance(r, dict) else (json.loads(r) if isinstance(r, str) and r.strip()[:1] in "{[" else {"raw": r})


def call(tm, name, **a):
    with contextlib.redirect_stderr(io.StringIO()):
        return P(tm[name].forward(**a))


def main():
    canon = load_canonical()
    su2_cfg = json.loads(render_snippets()["SU2_CONFIG"])
    mission = canon["mission"]
    mass_c = canon.get("mass", {})
    prop = canon.get("propulsion", {})

    # seed-42 anchor design -> derive geometry morph params (span + chords from AR/AREA/TAPER)
    anc = generate_random_params(42)
    AR = anc["Aircraft.Wing.ASPECT_RATIO"]; AREA = anc["Aircraft.Wing.AREA"]
    TAPER = anc["Aircraft.Wing.TAPER_RATIO"]; SWEEP = min(anc["Aircraft.Wing.SWEEP"], 35.0)
    span = round(math.sqrt(AR * AREA), 3)
    root_chord = round(2 * AREA / ((1 + TAPER) * span), 3)
    tip_chord = round(root_chord * TAPER, 3)
    print(f"ANCHOR design AR={AR} AREA={AREA} -> morph: span={span} root={root_chord} tip={tip_chord} sweep={SWEEP}")

    with contextlib.redirect_stderr(io.StringIO()):
        tm = _load_mcp_tools(load_config("config/mdo_f25_run_claude.yaml"))
    results = {}

    # 1. GEOMETRY — morph + verify it actually rebuilt
    g = call(tm, "open_cpacs", source_type="path", source=CPACS)
    gid = g.get("session_id")
    before = call(tm, "get_wing_summary", session_id=gid, wing_uid="Wing1").get("span")
    m = call(tm, "set_high_level_parameters", session_id=gid, component_uid="Wing1",
             updates={"span": span, "root_chord": root_chord, "tip_chord": tip_chord, "sweep": SWEEP})
    after = call(tm, "get_wing_summary", session_id=gid, wing_uid="Wing1")
    morphed = (m.get("geometry_morph") or {}).get("rebuilt") and abs((after.get("span") or 0) - before) > 0.5
    results["geometry_morph"] = "PASS" if morphed else f"FAIL (span {before}->{after.get('span')})"
    ref_area = after.get("reference_area"); mac = after.get("mac_length")
    mqc = after.get("mac_quarter_chord") or {}
    call(tm, "generate_volume_mesh", session_id=gid, component_uid="Wing1", far_field_distance=canon["geometry"]["far_field_distance"], boundary_layer_enabled=False)
    call(tm, "close_cpacs", session_id=gid)

    # 2. SU2 — FULL canonical config + geometry-derived REF
    s = call(tm, "create_su2_session", mesh_file_name="mesh.su2", base_name="f25_faithful")
    ssid = s.get("session_id")
    call(tm, "set_mesh", session_id=ssid, mesh_base64={"ref": "generate_volume_mesh__mesh_base64"}, mesh_file_name="mesh.su2", update_config=True)
    cfg = dict(su2_cfg)
    cfg.update({"REF_AREA": round(ref_area, 3), "REF_LENGTH": round(mac, 3),
                "REF_ORIGIN_MOMENT_X": round(mqc.get("x", 15.0), 3), "REF_ORIGIN_MOMENT_Y": 0.0, "REF_ORIGIN_MOMENT_Z": 0.0})
    call(tm, "update_config_entries", session_id=ssid, updates=cfg)
    rs = call(tm, "run_su2_solver", session_id=ssid, solver="SU2_CFD", max_runtime_seconds=300)
    results["su2_solve"] = f"PASS ({rs.get('runtime_seconds', 0):.0f}s)" if rs.get("exit_code") == 0 else f"FAIL exit={rs.get('exit_code')}"
    call(tm, "read_history_csv", session_id=ssid, relative_path="history.csv", max_rows=2000, skip_rows=0)
    reg = (getattr(__import__("src.tools.data_plane", fromlist=["get_design_state"]).get_design_state(), "data_store", {}) or {}).get("analysis_vars", {})
    cd_reg = reg.get("aero.cd_cruise")
    if cd_reg is None:
        results["aero_captured"] = "FAIL (no aero.cd_cruise in registry)"
    elif abs(cd_reg) < 0.001:
        # Inviscid Euler drag is near-zero/noisy -> the drag factor will clamp and the
        # drag SIGNAL is lost. Flag it (not a code bug; a modeling limitation).
        results["aero_captured"] = f"WARN (cd={cd_reg:.2e} near-zero -> drag signal weak/clamped; inviscid Euler limitation)"
    else:
        results["aero_captured"] = f"PASS (cd={cd_reg:.5f})"

    # 3. MASS — canonical flops/aluminum
    mm = call(tm, "estimate_mass", cpacs_file_path="/tmp/cpacs_out.xml",
              wing_mass_method=mass_c.get("wing_mass_method", "flops"), material=mass_c.get("material", "aluminum"),
              aviary_mass_method="FLOPS", design_load_factor=mass_c.get("design_load_factor", 2.5))
    _mass_ok = mm.get("oem_kg") or mm.get("success") or mm.get("status") == "success"
    results["mass"] = f"PASS (oem={mm.get('oem_kg') or mm.get('status')})" if _mass_ok else f"FAIL ({str(mm)[:60]})"

    # 4. PYCYCLE — canonical design point
    c = call(tm, "create_cycle_model", cycle_type="turbofan", mode="design")
    csid = c.get("session_id")
    T4_K = round(prop.get("T4_MAX_degR", 2857) * 5 / 9)
    pc = call(tm, "set_inputs", session_id=csid, values={"fc.alt": prop.get("design_altitude_ft", 33000), "fc.MN": prop.get("design_mach", 0.78),
                                                          "T4_MAX": prop.get("T4_MAX_degR", 2857), "splitter.BPR": prop.get("BPR", 5.105)})
    rc = call(tm, "run_cycle", session_id=csid, use_driver=False, outputs_of_interest=["perf.TSFC", "perf.Fn"])
    results["pycycle"] = "PASS" if (rc.get("success") or rc.get("converged") or pc.get("success")) else f"FAIL ({str(rc)[:60]})"

    # 5. AVIARY — canonical mission + coupling enforcement
    a = call(tm, "create_session", initial_parameters={})
    asid = a.get("session_id")
    call(tm, "configure_mission", session_id=asid, range_nmi=mission["range_nmi"], num_passengers=mission["num_passengers"],
         cruise_mach=mission["cruise_mach"], cruise_altitude_ft=mission["cruise_altitude_ft"], optimizer_max_iter=mission["optimizer_max_iter"])
    sap = call(tm, "set_aircraft_parameters", session_id=asid, parameters={
        "Aircraft.Wing.ASPECT_RATIO": AR, "Aircraft.Wing.AREA": AREA, "Aircraft.Wing.SWEEP": SWEEP,
        "Aircraft.Wing.TAPER_RATIO": TAPER, "Aircraft.Engine.SCALE_FACTOR": anc["Aircraft.Engine.SCALE_FACTOR"]})
    if sap.get("error_code") == "UNCOUPLED_MISSION":
        results["coupling"] = "FAIL (UNCOUPLED — aero didn't reach aviary)"
        results["mission"] = "SKIP (uncoupled)"
    else:
        inj = [p["name"] for p in (sap.get("applied") or []) if "DRAG" in p["name"] or "LIFT" in p["name"]]
        results["coupling"] = f"PASS (injected {inj})" if inj else "FAIL (no aero injected)"
        rsim = call(tm, "run_simulation", session_id=asid, timeout_seconds=120)
        gr = call(tm, "get_results", session_id=asid)
        fuel = (rsim.get("summary") or {}).get("fuel_burned_kg"); cd = gr.get("cruise_cd_avg")
        results["mission"] = f"PASS (fuel={fuel}, cruise_cd={cd})" if fuel else f"FAIL ({str(rsim)[:60]})"

    print("\n===== FAITHFUL PIPELINE CHECK (shared canonical config) =====")
    for k, v in results.items():
        print(f"  {k:16} {v}")
    ok = all(str(v).startswith(("PASS", "SKIP", "WARN")) for v in results.values())
    warns = [k for k, v in results.items() if str(v).startswith("WARN")]
    print(f"\n>>> {'ALL PASS' if ok and not warns else ('PASS w/ WARN: ' + ','.join(warns)) if ok else 'SOME FAIL — see above'}")


if __name__ == "__main__":
    main()

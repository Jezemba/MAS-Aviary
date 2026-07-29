"""Run the FULL MDO pipeline (geometry->SU2->mass->pycycle->aviary) in ONE process,
with the data-plane middleware active, so the mass wing-mass + SU2 CL/CD get injected
into aviary exactly like the real agent. Runs the same design TWICE to test whether
the FULL pipeline is reproducible (my earlier probe was aviary-only, hence the 9100 vs
12747 gap). Prints fuel + injected CL/CD/wing so we can see the coupling.
"""
import io, json, sys, contextlib
sys.path.insert(0, ".")
from src.config.loader import load_config
from scripts.stat_batch_runner import _load_mcp_tools

CPACS = "/home/aipexws3/Jessica/Avion/mass-mcp/tests/fixtures/D150_simple.xml"


def P(r):
    return r if isinstance(r, dict) else (json.loads(r) if isinstance(r, str) and r.strip()[:1] in "{[" else {"raw": r})


def call(tm, name, **args):
    with contextlib.redirect_stderr(io.StringIO()):
        return P(tm[name].forward(**args))


def full_eval(tm, design, label, run_su2=True):
    """One full-pipeline evaluation. Middleware auto-injects session_id/cpacs/mass/CL."""
    # geometry
    g = call(tm, "open_cpacs", source_type="path", source=CPACS)
    gsid = g.get("session_id")
    span = round((design["Aircraft.Wing.ASPECT_RATIO"] * design["Aircraft.Wing.AREA"]) ** 0.5, 2)
    call(tm, "set_high_level_parameters", session_id=gsid, component_uid="Wing1",
         updates={"span": span, "sweep": design["Aircraft.Wing.SWEEP"]})
    call(tm, "generate_volume_mesh", session_id=gsid, component_uid="Wing1",
         far_field_distance=10.0, boundary_layer_enabled=False)
    call(tm, "close_cpacs", session_id=gsid)
    cl = cd = None
    if run_su2:
        s = call(tm, "create_su2_session", mesh_file_name="mesh.su2", base_name="f25_fp")
        ssid = s.get("session_id")
        call(tm, "set_mesh", session_id=ssid, mesh_base64={"ref": "generate_volume_mesh__mesh_base64"},
             mesh_file_name="mesh.su2", update_config=True)
        call(tm, "update_config_entries", session_id=ssid,
             updates={"AOA": 2.0, "CFL_NUMBER": 20, "CFL_ADAPT": "YES", "MACH_NUMBER": 0.78})
        call(tm, "run_su2_solver", session_id=ssid, solver="SU2_CFD", max_runtime_seconds=300)
        # read the whole history; the LAST row is the converged state (middleware
        # _capture_aero_coefficients reads rows[-1] and cleans the quoted CSV keys).
        hist = call(tm, "read_history_csv", session_id=ssid, relative_path="history.csv", max_rows=2000, skip_rows=0)
        rows = hist.get("rows") or []
        last = rows[-1] if rows else {}
        cl = last.get("CL") or next((v for k, v in last.items() if isinstance(k, str) and k.strip().strip('"') == "CL"), None)
        cd = last.get("CD") or next((v for k, v in last.items() if isinstance(k, str) and k.strip().strip('"') == "CD"), None)
    # mass (wing mass captured by middleware for injection)
    call(tm, "estimate_mass", cpacs_file_path="/tmp/cpacs_out.xml",
         wing_mass_method="flops", material="aluminum", aviary_mass_method="FLOPS", design_load_factor=2.5)
    # aviary (middleware injects wing mass + CL/CD into set_aircraft_parameters)
    a = call(tm, "create_session", initial_parameters={})
    asid = a.get("session_id")
    call(tm, "configure_mission", session_id=asid, range_nmi=2500, num_passengers=239,
         cruise_mach=0.78, cruise_altitude_ft=33000, optimizer_max_iter=200)
    sap = call(tm, "set_aircraft_parameters", session_id=asid, parameters=design)
    rs = call(tm, "run_simulation", session_id=asid, timeout_seconds=300)
    gr = call(tm, "get_results", session_id=asid)
    summ = rs.get("summary") or {}
    injected = [p for p in (sap.get("applied") or []) if "MASS" in str(p.get("name", "")).upper()]
    print(f"  [{label}] fuel={summ.get('fuel_burned_kg')} iters={rs.get('iterations')} "
          f"gtow={summ.get('gtow_kg')} | SU2 CL={cl} CD={cd} | aviary cl={gr.get('cruise_cl_avg')} "
          f"cd={gr.get('cruise_cd_avg')} wing={gr.get('wing_mass_kg')}")
    if injected:
        print(f"        middleware injected into aviary: {injected}")
    return summ.get("fuel_burned_kg")


def main():
    run_su2 = "--nosu2" not in sys.argv
    with contextlib.redirect_stderr(io.StringIO()):
        tm = _load_mcp_tools(load_config("config/mdo_f25_run_claude.yaml"))
    design = {"Aircraft.Wing.AREA": 130.1, "Aircraft.Wing.ASPECT_RATIO": 11.0, "Aircraft.Wing.SWEEP": 25.0,
              "Aircraft.Wing.TAPER_RATIO": 0.278, "Aircraft.Fuselage.LENGTH": 37.79,
              "Aircraft.Fuselage.MAX_HEIGHT": 4.06, "Aircraft.Fuselage.MAX_WIDTH": 3.76,
              "Aircraft.Engine.SCALE_FACTOR": 1.3}
    print(f"FULL PIPELINE probe (run_su2={run_su2}) — design AR11/AREA130.1 (agent recorded 9100; aviary-only probe=12747)")
    f1 = full_eval(tm, design, "run 1", run_su2)
    f2 = full_eval(tm, design, "run 2", run_su2)
    print(f"\n>>> FULL-PIPELINE reproducible: {f1 == f2}  (run1={f1} run2={f2})")


if __name__ == "__main__":
    main()

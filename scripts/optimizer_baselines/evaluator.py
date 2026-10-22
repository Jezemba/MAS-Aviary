"""One coupled evaluation of a design, with no LLM: the same tools, order and data-plane coupling the agents get.

open the baseline CPACS -> morph (set_high_level_parameters) -> export -> volume mesh -> SU2 (the data plane
trims it to the cruise lift) -> mass on the morphed geometry -> pycycle sized from mass and drag (B187) ->
aviary with the framework's coupled values (CL, drag factor, wing mass scaler, engine scale, geometry
authority) -> run_simulation -> fuel burn and GTOW.

Every call goes through the middleware-wrapped tools (stat_batch_runner._load_mcp_tools), so what reaches
the solvers is exactly what an agent's call would deliver. Each evaluation starts from a fresh design state and
a fresh aviary session configured with the canonical mission, and records its tool calls in its own
knowledge-base link (chain_link = evaluation index) so the once-only guard (B81) never refuses a later one.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from design_space import Variable


def _json(out: Any) -> dict:
    if isinstance(out, dict):
        return out
    try:
        return json.loads(out)
    except (TypeError, ValueError):
        return {"raw": str(out)[:500]}


class CoupledEvaluator:
    def __init__(self, config_path: str, out_dir: Path, coarse_mesh: bool = True, port_offset: int = 0,
                 tigl_port: int | None = None, server_ports: dict | None = None):
        import os
        import sys

        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root))
        sys.path.insert(0, str(root / "scripts"))
        if coarse_mesh:
            os.environ["AVION_COARSE_MESH"] = "1"
        import stat_batch_runner as sbr
        from src.config.loader import load_config

        self.sbr = sbr
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        config = load_config(config_path)
        if port_offset:
            # A separate set of MCP servers (e.g. 9500/9200/...), so the baselines never load the study's servers.
            import re as _re

            for server in config.mcp.servers:
                server.url = _re.sub(r":(\d+)/", lambda m: f":{int(m.group(1)) + port_offset}/", server.url, count=1)
        if tigl_port:
            # Meshing blocks a tigl server entirely, so parallel workers each get their own (run 1, 2026-10-22:
            # the other workers' calls timed out while one meshed).
            import re as _re

            for server in config.mcp.servers:
                if server.name == "tigl":
                    server.url = _re.sub(r":(\d+)/", f":{tigl_port}/", server.url, count=1)
        if server_ports:
            # A whole server set per worker: tigl blocks while meshing and su2 while solving (run 2, 2026-10-22).
            import re as _re

            for server in config.mcp.servers:
                if server.name in server_ports:
                    server.url = _re.sub(r":(\d+)/", f":{server_ports[server.name]}/", server.url, count=1)
        self.server_urls = [server.url for server in config.mcp.servers]
        self.tools = sbr._load_mcp_tools(config)
        self.n_evaluations = 0

    def _call(self, name: str, **kwargs) -> dict:
        tool = self.tools.get(name)
        if tool is None:
            return {"error": f"{name} is not loaded"}
        try:
            return _json(tool.forward(**kwargs))
        except Exception as exc:  # a failed tool is a failed evaluation, never a crash of the optimizer
            return {"error": f"{name}: {type(exc).__name__}: {exc}"}

    def evaluate(self, x: list[float], vs: tuple[Variable, ...]) -> dict:
        """Fuel burn and GTOW for design x. Never raises; a failed step is recorded with its reason."""
        from src.config.canonical import load_canonical
        from src.tools.data_plane import get_design_state, register_presession, reset_design_state
        from src.tools.knowledge_base import configure_run

        index = self.n_evaluations
        self.n_evaluations += 1
        t0 = time.time()
        rec: dict = {"design": {v.name: float(xi) for v, xi in zip(vs, x)}, "steps": {}}

        def step(name: str, out: dict) -> bool:
            ok = not out.get("error") and out.get("success") is not False and not out.get("error_code")
            rec["steps"][name] = "ok" if ok else str(out.get("error_code") or out.get("error") or out)[:300]
            return ok

        try:
            reset_design_state()
            configure_run(path=str(self.out_dir / "knowledge_base.jsonl"), chain_link=index, attempt=1)
            mission = load_canonical().get("mission", {}) or {}
            setup = self.sbr.setup_session_with_params(self.tools, self.sbr.anchor_params_for("baseline", 42))
            register_presession("aviary", setup["session_id"])
            aviary_sid = setup["session_id"]

            # Geometry: open the baseline, morph, export the morph to this evaluation's own file.
            from src.coordination.graph_preinit import baseline_cpacs

            opened = self._call("open_cpacs", source_type="path", source=baseline_cpacs())
            if not step("open_cpacs", opened):
                return self._done(rec, t0, "open_cpacs failed")
            tigl_sid = (get_design_state().sessions or {}).get("tigl")
            by_component: dict[str, dict] = {}
            for v, xi in zip(vs, x):
                if v.tool == "tigl":
                    by_component.setdefault(v.target, {})[v.key] = float(xi)
            for uid, updates in by_component.items():
                if not step(f"morph {uid}", self._call("set_high_level_parameters", session_id=tigl_sid,
                                                       component_uid=uid, updates=updates)):
                    return self._done(rec, t0, f"morph {uid} failed")
            cpacs = str(self.out_dir / "cpacs" / f"eval_{index:05d}.xml")
            Path(cpacs).parent.mkdir(parents=True, exist_ok=True)
            if not step("export_cpacs", self._call("export_cpacs", session_id=tigl_sid, output_path=cpacs)):
                return self._done(rec, t0, "export_cpacs failed")
            mesh = self._call("generate_volume_mesh", session_id=tigl_sid, output_format="su2")
            if not step("generate_volume_mesh", mesh):
                return self._done(rec, t0, "mesh failed")
            rec["mesh_elements"] = (mesh.get("statistics") or {}).get("element_count")

            # Aero: SU2 on the morphed geometry; the data plane injects the trim target CL and the mesh.
            su2 = self._call("create_su2_session", cpacs_file_path=cpacs,
                             cruise_mach=mission.get("cruise_mach", 0.78),
                             cruise_altitude_ft=mission.get("cruise_altitude_ft", 33000))
            if not step("create_su2_session", su2):
                return self._done(rec, t0, "SU2 session failed")
            su2_sid = (get_design_state().sessions or {}).get("su2")
            solve = self._call("run_su2_solver", session_id=su2_sid, max_runtime_seconds=3600)
            if not step("run_su2_solver", solve):
                return self._done(rec, t0, "SU2 solve failed")

            # Mass on the morphed geometry, then the engine sized from mass and drag.
            if not step("estimate_mass", self._call("estimate_mass", cpacs_file_path=cpacs)):
                return self._done(rec, t0, "mass failed")
            cycle = self._call("create_cycle_model", cycle_type="turbofan", mode="design")
            step("create_cycle_model", cycle)
            cyc_sid = (get_design_state().sessions or {}).get("pycycle")
            engine_values = {v.target: float(xi) for v, xi in zip(vs, x) if v.tool == "pycycle"}
            step("set_inputs", self._call("set_inputs", session_id=cyc_sid, values=engine_values))
            if not step("run_cycle", self._call("run_cycle", session_id=cyc_sid)):
                return self._done(rec, t0, "engine cycle failed")

            # Mission: the framework applies the coupled values on set_aircraft_parameters.
            step("set_aircraft_parameters", self._call("set_aircraft_parameters", session_id=aviary_sid,
                                                       parameters={}))
            sim = self._call("run_simulation", session_id=aviary_sid, timeout_seconds=600)
            step("run_simulation", sim)
            summary = sim.get("summary") if isinstance(sim.get("summary"), dict) else sim
            fuel, gtow = summary.get("fuel_burned_kg"), summary.get("gtow_kg")
            rec["fuel_burned_kg"] = float(fuel) if fuel is not None else None
            rec["gtow_kg"] = float(gtow) if gtow is not None else None
            rec["flown"] = rec["fuel_burned_kg"] is not None and rec["fuel_burned_kg"] > 100.0
            rec["converged"] = sim.get("converged")
            store = get_design_state().data_store
            rec["coupled"] = {"cl": store.get("aero_cl_cruise"), "cd": store.get("aero_cd_cruise"),
                              "mtom_kg": store.get("mass_mtom_kg"), "wing_mass_kg": store.get("mass_wing_kg"),
                              "engine_scale": store.get("engine_injected_scale_factor")}
            for name, sid in (("close_su2_session", su2_sid), ("close_cycle_model", cyc_sid),
                              ("close_cpacs", tigl_sid)):
                if sid:
                    self._call(name, session_id=sid)       # sessions only; the SU2 work folder is kept
            return self._done(rec, t0, None)
        except Exception as exc:  # pragma: no cover - never let one design stop the optimizer
            return self._done(rec, t0, f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _done(rec: dict, t0: float, failure: str | None) -> dict:
        rec["failure"] = failure
        rec.setdefault("fuel_burned_kg", None)
        rec.setdefault("gtow_kg", None)
        rec.setdefault("flown", False)
        rec["seconds"] = round(time.time() - t0, 1)
        return rec

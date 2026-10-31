"""eval_counts: a flight is a successful run_simulation above the zero-fuel floor; other tools are counted, not charged."""

import json

from analysis.link20.eval_counts import evaluation_counts, mas_link_counts, optimizer_table


def _kb(tool, server, status="success", fuel=None):
    out = {"summary": {"fuel_burned_kg": fuel, "gtow_kg": 90000}} if fuel is not None else {}
    return {"tool": tool, "server": server, "status": status, "outputs": out}


def test_mas_flights_and_tool_counts():
    kb = [_kb("set_high_level_parameters", "tigl"), _kb("run_su2_solver", "su2"),
          _kb("run_su2_solver", "su2", status="failed"),
          _kb("run_simulation", "aviary", fuel=12000), _kb("run_simulation", "aviary", fuel=0.0),
          _kb("run_simulation", "aviary", status="failed"), _kb("get_results", "aviary", fuel=12000)]
    c = mas_link_counts(kb)
    assert c["flights"] == 1                              # zero-fuel run and get_results are not flights
    assert (c["simulation_calls"], c["simulation_ok"]) == (3, 2)
    assert (c["su2_calls"], c["su2_ok"]) == (2, 1)
    assert c["morph_calls"] == 1 and c["mesh_calls"] == 0


def test_optimizer_evaluation_stops_at_failed_step():
    rec = {"steps": {"morph Wing1": "ok", "morph Fuselage1": "ok", "generate_volume_mesh": "ok",
                     "run_su2_solver": "NO_MESH"}, "flown": False, "fuel_burned_kg": None}
    c = evaluation_counts(rec)
    assert c["morph_calls"] == 1 and c["morph_ok"] == 1   # both components = one morph
    assert (c["su2_calls"], c["su2_ok"]) == (1, 0)
    assert c["simulation_calls"] == 0 and c["flights"] == 0


def test_optimizer_table_sums_per_iteration(tmp_path):
    run = tmp_path / "ga"
    run.mkdir()
    ok = {"run_simulation": "ok"}
    rows = [{"kind": "evaluation", "iteration": 1, "steps": ok, "flown": True, "fuel_burned_kg": 9000},
            {"kind": "evaluation", "iteration": 1, "steps": ok, "flown": True, "fuel_burned_kg": 9500},
            {"kind": "iteration", "iteration": 1}]
    (run / "evaluations.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    t = optimizer_table([run])
    assert t.evaluations.tolist() == [2] and t.flights.tolist() == [2] and t.optimizer.tolist() == ["ga"]

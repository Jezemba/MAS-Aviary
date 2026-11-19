"""Validation 1 (2026-11-18) fixes on fix/validation1-defaults: V2, V3, V4, V6, V7, V8, V10, V12, V14, and the
link 1 -> link 2 carry-forward (Jessica's hard requirement: what an iteration ends with is exactly what the next starts
from).

Fixtures are recorded validation-1 data (tests/fixtures/v1b/, copied from ws8 /data/Jessica/final_val):
  s_if_link1/                     S_IF (ws3) link 1: design.json, design_flown.json, result.json chain fields
  s_if_link2_carry_over_recorded  the carry_over.json the live runner wrote for S_IF link 2
  s_if_link2_first_calls          S_IF link 2's first open_cpacs / estimate_mass / get_cruise_point / set_inputs /
                                  configure_mission arguments as the servers received them
  n_if_link1_fuselage             N_IF (ws8) link 1: tigl get_fuselage_summary reply + aviary's held fuselage (V4)
  o_sp_link1_attempt1             O_SP (ws6) link 1 attempt 1: the order agents ran (V6) and the V14 call
No network, no API, no MCP server: every tool is a local stub.
"""
import json
import re
from pathlib import Path

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures" / "v1b"
TABLE_SOURCES = ["sequential.md", "orchestrated.md", "networked.md", "config/mdo_f25_sequential_agents.yaml"]

# tigl-mcp 043ba39 on the unmorphed D150, get_high_level_parameters as recorded in every validation-1 link
# (ws8 /data/Jessica/final_val/runs/val_net_graph, val_net_iter; ws5 val_seq_graph/val_seq_staged).
MEASURED = {
    "Wing1": {"area": 122.82871858371793, "aspect_ratio": 9.363224080853465, "sweep": 21.65015342508847},
    "Wing2H": {"area": 31.118412165407747, "aspect_ratio": 4.980972880884807, "sweep": 27.999928617646056},
    "Wing3V": {"area": 21.500003892069813, "aspect_ratio": 1.6069355731255068, "sweep": 37.19996203995765},
    "Fuselage1": {"length": 37.644560994515366, "diameter": 4.143709313380411},
}
MEASURED_FUSELAGE_HW = {"max_height": 4.143709313380411, "max_width": 3.951781358688922}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for var in ("AVION_REQUIRE_PHYSICAL_AERO", "AVION_ENGINE_DECK", "AVION_REQUIRE_COUPLED_INPUTS",
                "AVION_DESIGN_AUTHORITY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AVION_CPACS_TAILS", "0")
    monkeypatch.setenv("AVION_CPACS_WING_TC", "0")
    state = DesignState()
    monkeypatch.setattr(dp, "_design_state", state)
    monkeypatch.setattr(dp, "_registered_tools", {})
    monkeypatch.setattr(dp, "_presessions", {})
    for name in ("morph", "wing_uid", "fuselage_uid", "engine_pending", "refusal"):
        if hasattr(dp._call_ctx, name):
            delattr(dp._call_ctx, name)
    yield state


def _load(name):
    return json.loads((FIX / name).read_text())


# ---- V3: every prompt table's starting design is what the pinned tigl measures ---------------------------------

_SET_ROW = re.compile(r"\| Set \| tigl: `set_high_level_parameters`, [^|]*`(\w+)`, key `(\w+)` \| [^|]* \| ([^|]+) \|")
_DERIVED = re.compile(r"\| Fuselage max (height|width) \| Derived \| [^|]* \| [^|]* \| ([^|]+) \|")


@pytest.mark.parametrize("source", TABLE_SOURCES)
def test_v3_every_set_default_is_the_measured_d150(source):
    text = (REPO / source).read_text(encoding="utf-8")
    rows = _SET_ROW.findall(text)
    assert rows
    for uid, key, default in rows:
        value = float(default.strip())
        want = MEASURED[uid][key]
        # The tables give 4 decimals for areas / AR / fuselage and 2 for sweeps; tigl's morph is a no-op within
        # 1e-4 relative (area, AR) and 0.01 deg (sweep) -- the fuselage has no tolerance, see the report.
        tol = 0.005 if key == "sweep" else 0.5e-4 + 1e-12
        assert abs(value - want) <= tol, (source, uid, key, value, want)
    for which, default in _DERIVED.findall(text):
        assert float(default.strip()) == round(MEASURED_FUSELAGE_HW[f"max_{which}"], 4), (source, which)
    for stale in ("| 37.6843 |", "| 4.1490 |", "| 4.15 | follows diameter", "| 3.96 | follows diameter"):
        assert stale not in text, (source, stale)


def test_v3_all_seven_table_copies_were_changed():
    counts = {s: (REPO / s).read_text(encoding="utf-8").count("| 37.6446 |") for s in TABLE_SOURCES}
    assert counts == {"sequential.md": 1, "orchestrated.md": 3, "networked.md": 2,
                      "config/mdo_f25_sequential_agents.yaml": 1}


def test_v3_link1_design_state_shows_the_measured_fuselage():
    from src.coordination import prompt_assembly

    text = prompt_assembly.starting_design_state(prompt_assembly.system_text("orchestrated.md",
                                                                             "orchestrated_staged_pipeline"))
    assert "Fuselage1: length 37.6446, diameter 4.1437" in text
    assert "37.6843" not in text and "4.1490" not in text


def test_v3_canonical_anchor_is_the_recorded_measurement():
    from src.config.canonical import anchor_geometry_params

    exact = anchor_geometry_params(rounded=False)
    assert exact["Aircraft.Fuselage.LENGTH"] == MEASURED["Fuselage1"]["length"]
    assert exact["Aircraft.Fuselage.MAX_HEIGHT"] == MEASURED_FUSELAGE_HW["max_height"]
    assert exact["Aircraft.Fuselage.MAX_WIDTH"] == MEASURED_FUSELAGE_HW["max_width"]
    assert exact["Aircraft.Wing.AREA"] == MEASURED["Wing1"]["area"]
    assert exact["Aircraft.Wing.SWEEP"] == MEASURED["Wing1"]["sweep"]
    # The table's 4-decimal defaults are this anchor rounded.
    assert anchor_geometry_params()["Aircraft.Fuselage.LENGTH"] == 37.6446


# ---- V4: the measured fuselage flies -----------------------------------------------------------------------------

def test_v4_runner_baseline_params_are_the_measured_anchor_not_constants():
    from scripts.stat_batch_runner import BASELINE_PARAMS

    assert BASELINE_PARAMS["Aircraft.Fuselage.LENGTH"] == 37.6446
    assert BASELINE_PARAMS["Aircraft.Fuselage.MAX_HEIGHT"] == 4.1437
    assert BASELINE_PARAMS["Aircraft.Fuselage.MAX_WIDTH"] == 3.9518
    assert BASELINE_PARAMS["Aircraft.Wing.AREA"] == 122.8287
    assert BASELINE_PARAMS["Aircraft.Wing.ASPECT_RATIO"] == 9.3632
    # S_IF link 1 (validation 1) ended with exactly these aviary values: the anchor equals what the geometry
    # authority sends, so a link nobody measured the fuselage in flies the same aircraft as one that did.
    end = _load("s_if_link1/result_subset.json")["chain_end_params"]
    assert {k: v for k, v in BASELINE_PARAMS.items() if k != "Aircraft.Engine.SCALE_FACTOR"} == \
        {k: v for k, v in end.items() if k != "Aircraft.Engine.SCALE_FACTOR"}


def test_v4_get_fuselage_summary_is_captured_from_the_recorded_reply(fresh):
    row = _load("n_if_link1_fuselage.json")["get_fuselage_summary"]
    dp._call_ctx.fuselage_uid = row["args"]["fuselage_uid"]
    dp._capture_geometry_fuselage("get_fuselage_summary", row["result"])
    rec = fresh.data_store["geometry_fuselage"]
    assert rec["Aircraft.Fuselage.LENGTH"] == 37.644560994515366
    assert rec["Aircraft.Fuselage.MAX_HEIGHT"] == 4.143709313380411
    assert rec["Aircraft.Fuselage.MAX_WIDTH"] == 3.951781358688922
    assert rec["_source"] == "get_fuselage_summary"


def test_v4_the_n_if_link1_set_now_flies_the_measured_fuselage(fresh):
    """N_IF link 1: the peer read the fuselage with get_fuselage_summary only; aviary kept 37.68 / 4.15 / 3.96."""
    rec = _load("n_if_link1_fuselage.json")
    assert rec["get_results_aircraft_params"]["Aircraft.Fuselage.LENGTH"] == 37.68      # what flew (the defect)
    dp._call_ctx.fuselage_uid = "Fuselage1"
    dp._capture_geometry_fuselage("get_fuselage_summary", rec["get_fuselage_summary"]["result"])
    resolved = {"session_id": "av", "parameters": {"Aircraft.Fuselage.LENGTH": 37.68}}
    dp._apply_geometry_authority(resolved)
    assert resolved["parameters"]["Aircraft.Fuselage.LENGTH"] == 37.6446
    assert resolved["parameters"]["Aircraft.Fuselage.MAX_HEIGHT"] == 4.1437
    assert resolved["parameters"]["Aircraft.Fuselage.MAX_WIDTH"] == 3.9518


def test_v4_seed_then_measure_on_the_live_session(fresh):
    dp.seed_anchor_geometry()
    assert fresh.data_store["geometry_fuselage"]["_source"] == dp.ANCHOR_SOURCE
    assert fresh.data_store["geometry_wing"]["Aircraft.Wing.AREA"] == MEASURED["Wing1"]["area"]
    calls = []

    class _Measure:
        def forward(self, **kw):
            calls.append(kw)
            reply = {"component_uid": "Fuselage1", "measured": True,
                     "parameters": {"length": 37.644560994515366, "max_width": 3.951781358688922,
                                    "max_height": 4.143709313380411, "diameter": 4.143709313380411}}
            dp._capture_geometry_fuselage("get_high_level_parameters", reply)
            return reply

    dp._registered_tools["get_high_level_parameters"] = _Measure()
    fresh.sessions["tigl"] = "tigl-1"
    resolved = {"session_id": "av", "parameters": {}}
    dp._apply_geometry_authority(resolved)
    assert calls == [{"session_id": "tigl-1", "component_uid": "Fuselage1"}]
    assert fresh.data_store["geometry_fuselage"]["_source"] == "get_high_level_parameters"
    assert resolved["parameters"]["Aircraft.Fuselage.LENGTH"] == 37.6446
    dp._apply_geometry_authority({"session_id": "av", "parameters": {}})
    assert len(calls) == 1                       # measured once; not again while the measurement stands


def test_v4_no_measurement_without_a_live_session_or_with_a_pending_morph(fresh):
    calls = []
    dp._registered_tools["get_high_level_parameters"] = type("T", (), {"forward": lambda self, **kw: calls.append(kw)})()
    dp._apply_geometry_authority({"session_id": "av", "parameters": {}})        # no tigl session
    fresh.sessions["tigl"] = "tigl-1"
    fresh.data_store["morph_unexported"] = True
    dp._apply_geometry_authority({"session_id": "av", "parameters": {}})        # morph not exported
    assert calls == []


class _Tool:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def forward(self, **kw):
        self.calls.append(kw)
        return self.reply


def _runner_tools():
    return {"create_session": _Tool({"session_id": "av-new"}), "configure_mission": _Tool({"success": True})}


def test_v4_the_runner_seeds_only_a_first_link(fresh):
    from scripts.stat_batch_runner import BASELINE_PARAMS, setup_session_with_params, starts_from_anchor

    anchor = dict(BASELINE_PARAMS)
    assert starts_from_anchor("", None, "baseline", anchor, anchor) is True
    assert starts_from_anchor("/runs/repeat_000/c", None, "baseline", anchor, anchor) is False   # link 2+
    assert starts_from_anchor("", "/runs/repeat_000/c/geometry_end.xml", "baseline", anchor, anchor) is False
    assert starts_from_anchor("", None, "baseline", {**anchor, "Aircraft.Engine.SCALE_FACTOR": 1.2}, anchor) is False
    assert starts_from_anchor("", None, "random", anchor, anchor) is False
    tools = _runner_tools()
    out = setup_session_with_params(tools, anchor, seed_anchor=True)
    assert tools["create_session"].calls[0]["initial_parameters"] == anchor
    assert out["seeded_geometry"]["Aircraft.Fuselage.LENGTH"] == MEASURED["Fuselage1"]["length"]
    assert fresh.data_store["geometry_fuselage"]["_source"] == dp.ANCHOR_SOURCE


# ---- Jessica's hard requirement: link 1 -> link 2 carry-forward, field by field ------------------------------------

def _link2(tmp_path, monkeypatch):
    """Write link 2's carry_over exactly as the runner does after S_IF link 1, and point the framework at it."""
    import shutil

    from scripts import stat_batch_runner as sbr
    from src.coordination import prompt_assembly

    link1 = tmp_path / "repeat_000" / "combo"
    link1.mkdir(parents=True)
    for f in ("design.json", "design_flown.json"):
        shutil.copyfile(FIX / "s_if_link1" / f, link1 / f)
    res = _load("s_if_link1/result_subset.json")
    link2 = tmp_path / "repeat_001" / "combo"
    path = sbr.write_carry_over(link2, 1, "VERDICT: CONTINUE", res["chain_cpacs_out"], res["chain_end_params"],
                                sbr.design_space_of(link1), previous_dir=str(link1),
                                flown_dir=str(link1), flown_cpacs=res["chain_cpacs_out"])
    carry = json.loads(path.read_text())
    monkeypatch.setattr(prompt_assembly, "read_carry_over", lambda: carry)
    return link1, carry, res


def test_carry_link2_starts_exactly_where_link1_ended(tmp_path, monkeypatch, fresh):
    from scripts import stat_batch_runner as sbr
    from scripts.stat_batch_runner import BASELINE_PARAMS, setup_session_with_params, starts_from_anchor
    from src.coordination import prompt_assembly

    link1, carry, res = _link2(tmp_path, monkeypatch)
    flown = json.loads((link1 / "design_flown.json").read_text())
    space = sbr.design_space_of(link1)

    # 1. The agent-facing DESIGN_STATE of link 2 is link 1's design, not the table defaults.
    text = prompt_assembly.starting_design_state(prompt_assembly.system_text("networked.md",
                                                                             "networked_iterative_feedback"))
    assert "DESIGN_STATE (the starting design for this iteration, from the previous iteration's design.json):" in text
    assert "DESIGN_STATE (the starting design for this iteration):" not in text
    assert f"CPACS_FILE: {res['chain_cpacs_out']}" in text
    for uid, values in space["geometry"].items():
        assert f"  {uid}: {prompt_assembly._fmt(values)}" in text, uid
    assert prompt_assembly._fmt(space["engine"]) in text
    for key in ("T4_TAKEOFF", "T4_CLIMB", "T4_MAX", "splitter.BPR", "fan.PR", "lpc.PR", "hpc.PR"):
        assert key in space["engine"], key
    assert prompt_assembly._fmt(space["mission"]) in text
    assert "Fuselage1: length 37.6446, diameter 4.1437" not in text            # no table default line

    # 2. Data plane: the exact carried mass basis, take-off mass and engine of link 1's flight.
    mtom = flown["structures"]["mtom_kg"]
    assert dp.trim_mass_basis() == (mtom, "mass-mcp sized mass carried from the last flown iteration")
    assert dp._takeoff_mass_kg() == mtom                                          # V7: exact, not round(.., 1)
    deck, scale, _ = dp.carried_engine()
    assert deck == flown["engine_deck"]["file"]
    assert scale == flown["mission_applied"]["Aircraft.Engine.SCALE_FACTOR"]

    # 3. Server state: link 2's aviary session starts from link 1's end state; no anchor seed.
    params = dict(res["chain_end_params"])
    assert starts_from_anchor(str(link1), res["chain_cpacs_out"], "baseline", params, dict(BASELINE_PARAMS)) is False
    tools = _runner_tools()
    setup_session_with_params(tools, params, seed_anchor=False)
    assert tools["create_session"].calls[0]["initial_parameters"] == res["chain_end_params"]
    assert "geometry_fuselage" not in fresh.data_store and "geometry_wing" not in fresh.data_store

    # 4. The carry_over written here is the one the live runner wrote for S_IF link 2 (same keys and values; the
    #    folder paths differ only by where the copy lives).
    recorded = _load("s_if_link2_carry_over_recorded.json")
    for key in ("starting_design", "aviary_session_start", "link"):
        assert carry[key] == recorded[key], key
    for part in ("structures", "engine_deck", "mission_applied", "design_space", "aero", "aviary_cruise_point"):
        assert carry["last_flown_design"][part] == recorded["last_flown_design"][part], part


def test_carry_recorded_link2_calls_used_link1s_end_state():
    """What S_IF link 2's servers received (recorded): the geometry file, engine, mass basis and engine inputs of
    link 1's flight, field by field. The take-off mass was the one value not exact (90,298.6: V7, fixed)."""
    flown = json.loads((FIX / "s_if_link1" / "design_flown.json").read_text())
    res = _load("s_if_link1/result_subset.json")
    calls = _load("s_if_link2_first_calls.json")
    mtom = flown["structures"]["mtom_kg"]
    assert calls["open_cpacs"]["args"]["source"] == res["chain_cpacs_out"]
    assert calls["estimate_mass"]["args"]["engine_deck_file"] == flown["engine_deck"]["file"]
    assert calls["estimate_mass"]["args"]["engine_scale_factor"] == flown["mission_applied"]["Aircraft.Engine.SCALE_FACTOR"]
    assert calls["get_cruise_point"]["args"]["mass_kg"] == mtom
    assert calls["get_cruise_point"]["args"]["engine_scale"] == flown["mission_applied"]["Aircraft.Engine.SCALE_FACTOR"]
    for key, value in flown["design_space"]["engine"].items():
        assert calls["set_inputs"]["args"]["values"][key] == value, key
    assert calls["configure_mission"]["args"]["takeoff_mass_kg"] == round(mtom, 1) != mtom     # the V7 defect


# ---- V7: exact masses ----------------------------------------------------------------------------------------------

def test_v7_takeoff_mass_is_the_exact_basis(monkeypatch, fresh):
    from src.coordination import prompt_assembly

    monkeypatch.setattr(prompt_assembly, "read_carry_over",
                        lambda: {"previous_design": {"structures": {"mtom_kg": 90298.64252810457,
                                                                    "sizing_converged": True}}})
    assert dp._takeoff_mass_kg() == 90298.64252810457
    resolved = {"session_id": "av"}
    dp._inject_takeoff_mass(resolved)
    assert resolved["takeoff_mass_kg"] == 90298.64252810457


def test_v7_canonical_sized_mass_is_the_recorded_estimate_for_the_canonical_engine():
    from src.config.canonical import load_canonical

    base = load_canonical()["baseline"]
    assert base["sized_mtom_kg"] == 90298.6425281046            # ws8 baseline2 flights B2_coupled_anchor_1/_2
    assert base["engine_scale_factor"] == 1.1957967702892787
    flown = json.loads((FIX / "s_if_link1" / "design_flown.json").read_text())
    # The same estimate on ws3 (S_IF link 1, same engine): equal to 2 ulp (machine-level last digits).
    assert abs(flown["structures"]["mtom_kg"] - base["sized_mtom_kg"]) < 1e-9 * base["sized_mtom_kg"]


# ---- V6: the synthetic TASK_COMPLETE needs every stage ------------------------------------------------------------

def _pipeline_handler():
    from src.coordination.stage_definition import load_pipeline_from_yaml
    from src.coordination.staged_pipeline_handler import StagedPipelineHandler

    handler = StagedPipelineHandler({})
    handler._pipeline = load_pipeline_from_yaml(REPO / "config" / "mdo_f25_staged_pipeline.yaml")
    return handler


def _agents(names):
    from unittest.mock import MagicMock

    out = {}
    for n in names:
        a = MagicMock()
        a.run = MagicMock(return_value=f"{n} done")
        a.name, a.logs, a.description = n, [], ""
        out[n] = a
    return out


def test_v6_o_sp_attempt1_order_no_longer_ends_the_link_before_the_mission(monkeypatch):
    from src.coordination import prompt_assembly
    from src.coordination.execution_handler import Assignment

    monkeypatch.setattr(prompt_assembly, "is_verbatim", lambda: False)
    workers = [n for n in _load("o_sp_link1_attempt1.json")["agent_runs_attempt1"] if n != "orchestrator"]
    assert workers == ["geometry_engineer", "aerodynamics_analyst", "aerodynamics_analyst", "structures_analyst",
                       "structures_analyst", "propulsion_analyst", "propulsion_analyst"]
    handler = _pipeline_handler()
    stage_names = [s.name for s in handler._pipeline.stages]
    assert len(stage_names) == 7
    agents = _agents(set(stage_names))
    seen = []
    for name in workers:            # the orchestrated coordinator runs one assignment per execute()
        msgs = handler.execute([Assignment(agent_name=name, task="t")], agents, None)
        seen.append(msgs[-1].content)
    assert handler._stage_cursor == 7                       # the old trigger
    assert not any("TASK_COMPLETE" in c for c in seen)      # was appended to the 2nd propulsion reply
    assert [r.stage_name for r in handler.last_stage_results] == ["propulsion_analyst"]
    for name in ("mission_architect", "simulation_executor"):
        msgs = handler.execute([Assignment(agent_name=name, task="t")], agents, None)
        assert "TASK_COMPLETE" not in msgs[-1].content
    msgs = handler.execute([Assignment(agent_name=stage_names[-1], task="t")], agents, None)
    assert msgs[-1].content.endswith("TASK_COMPLETE")       # every stage ran


def test_v6_sequential_order_unchanged(monkeypatch):
    from src.coordination import prompt_assembly
    from src.coordination.execution_handler import Assignment

    monkeypatch.setattr(prompt_assembly, "is_verbatim", lambda: False)
    handler = _pipeline_handler()
    names = [s.name for s in handler._pipeline.stages]
    agents = _agents(names)
    contents = [handler.execute([Assignment(agent_name=n, task="t")], agents, None)[-1].content for n in names]
    assert [("TASK_COMPLETE" in c) for c in contents] == [False] * 6 + [True]
    assert [r.stage_name for r in handler.last_stage_results] == [names[-1]]


# ---- V8: token totals --------------------------------------------------------------------------------------------

def test_v8_local_generations_are_metered(monkeypatch):
    from smolagents.models import ChatMessage, TokenUsage, TransformersModel

    from src.llm import thinking_model
    from src.llm.cost_meter import METER

    METER.reset()
    monkeypatch.setattr(TransformersModel, "generate",
                        lambda self, msgs, **kw: ChatMessage(role="assistant", content="x",
                                                             token_usage=TokenUsage(input_tokens=1200,
                                                                                    output_tokens=34)))
    model = object.__new__(thinking_model.ThinkingModel)
    for _ in range(3):
        model._generate_once([{"role": "user", "content": "hi"}])
    snap = METER.snapshot()
    assert snap["calls"] == snap["local_calls"] == 3
    assert snap["input_tokens"] == 3600 and snap["output_tokens"] == 102 and snap["total_tokens"] == 3702
    METER.reset()


# ---- V10: no interim engine scale before the deck is attached -----------------------------------------------------

def test_v10_no_bench_referenced_scale_before_the_deck(monkeypatch, fresh):
    from src.tools import coupling

    monkeypatch.setattr(coupling, "get_var", lambda state, key: 7055.200944706749 if key == "prop.fn_lbf" else None)
    monkeypatch.setattr(dp, "engine_is_current", lambda: True)
    fresh.sessions["aviary"] = "av"
    resolved = {"session_id": "av", "parameters": {"Aircraft.Engine.SCALE_FACTOR": 1.18854}}
    dp._inject_engine_scale(resolved)
    assert "Aircraft.Engine.SCALE_FACTOR" not in resolved["parameters"]
    reply = {"success": True}
    dp._annotate_geometry_authority("set_aircraft_parameters", reply)
    assert "set by the framework when the designed engine's deck is attached" in reply["engine_scale"]
    assert "1.18854" in reply["engine_scale"]
    # With the deck attached the scale is against its 5,900 lbf reference.
    deck = "/decks/deck_7815057d6edc9732.deck"
    fresh.data_store["engine_deck_attached"] = {"av": deck}
    fresh.data_store["engine_deck_references"] = {deck: {"reference_fn_des_lbf": 5900.0, "key": "7815057d"}}
    resolved = {"session_id": "av", "parameters": {}}
    dp._inject_engine_scale(resolved)
    assert resolved["parameters"]["Aircraft.Engine.SCALE_FACTOR"] == pytest.approx(1.1957967702892787, rel=1e-12)


def test_v10_bench_engine_runs_keep_the_bench_reference(monkeypatch, fresh):
    from src.tools import coupling

    monkeypatch.setenv("AVION_ENGINE_DECK", "0")
    monkeypatch.setattr(coupling, "get_var", lambda state, key: 5936.0 if key == "prop.fn_lbf" else None)
    monkeypatch.setattr(dp, "engine_is_current", lambda: True)
    resolved = {"session_id": "av", "parameters": {}}
    dp._inject_engine_scale(resolved)
    assert resolved["parameters"]["Aircraft.Engine.SCALE_FACTOR"] == pytest.approx(
        5936.0 / coupling.BENCH_ENGINE_CRUISE_FN_LBF)


# ---- V12: scored against the task's requirements, reported against the coupled baseline ---------------------------

def test_v12_requirements_and_baseline_reference():
    from src.logging.eval_classifier import DEFAULT_AVIARY_THRESHOLDS as t
    from src.logging.eval_classifier import classify_aviary_eval

    assert t.limits == {"fuel_burned_kg": 15000.0, "gtow_kg": 90000.0}
    assert t.reference["fuel_burned_kg"] == 15719.17609579942 and t.reference["gtow_kg"] == 90310.00549723483
    base = classify_aviary_eval(dict(t.reference), converged=True)
    assert base.result == "omission" and not base.fuel_pass and not base.gtow_pass
    assert "fuel_burned_kg 15719.2 > requirement 15000; +0.00% vs the coupled baseline" in base.reason
    # S_SP / S_IF link 2 of validation 1: fuel 15,148.06 kg -- still above the 15,000 kg requirement.
    s2 = classify_aviary_eval({"fuel_burned_kg": 15148.0591441, "gtow_kg": 89980.0}, converged=True)
    assert not s2.fuel_pass and s2.gtow_pass and "-3.63% vs the coupled baseline" in s2.reason
    ok = classify_aviary_eval({"fuel_burned_kg": 14900.0, "gtow_kg": 89900.0, "wing_mass_kg": 9000.0},
                              converged=True)
    assert ok.result == "success" and ok.reason.startswith("All task requirements met")
    missing = classify_aviary_eval({"fuel_burned_kg": 14900.0}, converged=True)
    assert missing.result == "omission" and "gtow_kg not measured" in missing.reason


# ---- V14: another server's session id ------------------------------------------------------------------------------

def test_v14_aviary_session_sent_to_tigl_is_refused_with_the_way_out(monkeypatch, fresh):
    rec = _load("o_sp_link1_attempt1.json")
    call = rec["v14_call"]
    assert "Unknown session_id" in json.dumps(call["result"])                  # what tigl answered
    monkeypatch.setattr(dp, "_tool_server_map", {"set_high_level_parameters": "tigl", "open_cpacs": "tigl"})
    fresh.sessions["aviary"] = rec["aviary_session"]
    dp.take_refusal()
    dp.resolve_request(call["tool"], dict(call["args"]))
    refusal = dp.take_refusal()
    assert refusal["error_code"] == "SESSION_OF_ANOTHER_SERVER"
    assert rec["aviary_session"] in refusal["error"] and "aviary session" in refusal["error"]
    assert "open_cpacs" in refusal["error"]
    # Once tigl has a session, a wrong id is corrected as before (no refusal).
    fresh.sessions["tigl"] = rec["tigl_session_opened_after"]
    resolved = dp.resolve_request(call["tool"], dict(call["args"]))
    assert dp.take_refusal() is None and resolved["session_id"] == rec["tigl_session_opened_after"]


# ---- V2: check_coupling follows the B324 contract ------------------------------------------------------------------

def _link_dir(tmp_path, *, inject_cl=False, held_fuselage=None):
    """A link folder built from S_IF link 1's recorded design_flown.json and the calls that produced it."""
    flown = json.loads((FIX / "s_if_link1" / "design_flown.json").read_text())
    d = tmp_path / "repeat_000" / "combo"
    d.mkdir(parents=True)
    (d / "design_flown.json").write_text(json.dumps(flown))
    (d / "result.json").write_text(json.dumps({"attempt": 1, "chain_link": 0}))
    aero, point = flown["aero"], flown["aviary_cruise_point"]
    applied = dict(flown["mission_applied"])
    if inject_cl:
        applied["Mission.Design.LIFT_COEFFICIENT"] = aero["cl_cruise"]
    if held_fuselage:          # N_IF link 1: no set named the fuselage; the session kept the runner's values
        applied = {k: v for k, v in applied.items() if not k.startswith("Aircraft.Fuselage.")}
    from src.tools.coupling import fn_des_from_lift_to_drag

    rows = [
        {"tool": "get_fuselage_summary", "args": {"fuselage_uid": "Fuselage1"},
         "result": {"length": 37.644560994515366, "max_height": 4.143709313380411, "max_width": 3.951781358688922,
                    "measured": True}},
        {"tool": "run_su2_solver", "args": {"session_id": "su2"},
         "result": {"success": True, "CL": aero["cl_cruise"], "CD": aero["cd_cruise"]}},
        {"tool": "get_cruise_point", "args": {"mass_kg": point["mass_kg"]}, "result": {"cruise_point": point}},
        {"tool": "set_inputs", "args": {"values": {"Fn_DES": fn_des_from_lift_to_drag(point["mass_kg"],
                                                                                      point["lift_to_drag"])}},
         "result": {"updated": ["Fn_DES"]}},
        {"tool": "configure_mission", "args": {"takeoff_mass_kg": aero["solve_trim_basis"]["mass_kg"]},
         "result": {"success": True}},
        {"tool": "set_aircraft_parameters", "args": {"parameters": applied}, "result": {"success": True}},
    ]
    if held_fuselage:
        rows.append({"tool": "get_results", "args": {},
                     "result": {"design_parameters": {"aircraft_params": held_fuselage}}})
    rows.append({"tool": "run_simulation", "args": {},
                 "result": {"success": True, "fuel_burned_kg": 15719.17, "flown_design_fingerprint":
                            point["design_fingerprint"], "engine": {}}})
    (d / "tool_calls_attempt1.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    return d


def _checks(d):
    import scripts.check_coupling as cc

    link = cc.Link(d)
    mission, flight = link.final_mission()
    return {name: (status, detail) for name, status, detail in
            [cc.check_su2_to_mission(link, mission), cc.check_mass_basis(link, mission),
             cc.check_tails_tc(link, mission), cc.check_fuselage(link, mission)]
            + cc.check_cruise_point_engine(link, mission, flight)}


def test_v2_a_correct_flops_contract_link_has_no_fail(tmp_path):
    out = _checks(_link_dir(tmp_path))
    assert {k: v[0] for k, v in out.items()} == {
        "SU2 solved + recorded (not coupled)": "PASS", "trim mass basis": "PASS",
        "measured tails + wing t/c -> aviary": "PASS", "geometry -> aviary fuselage": "PASS",
        "cruise point = flown design": "PASS", "cruise point -> Fn_DES": "PASS"}, out


def test_v2_an_injected_su2_cl_and_the_runner_fuselage_fail(tmp_path):
    out = _checks(_link_dir(tmp_path, inject_cl=True,
                            held_fuselage={"Aircraft.Fuselage.LENGTH": 37.68, "Aircraft.Fuselage.MAX_HEIGHT": 4.15,
                                           "Aircraft.Fuselage.MAX_WIDTH": 3.96}))
    assert out["SU2 solved + recorded (not coupled)"][0] == "FAIL"
    assert "SU2 values reached aviary" in out["SU2 solved + recorded (not coupled)"][1]
    assert out["geometry -> aviary fuselage"][0] == "FAIL"       # N_IF link 1 (V4)


def test_v4_the_seed_never_stands_in_for_reading_the_solved_file(monkeypatch, fresh):
    dp.seed_anchor_geometry()
    plan = {"reference_area": 130.0, "aspect_ratio": 10.0, "span": 36.06, "sweep_quarter_chord_deg": 25.0,
            "taper_ratio": 0.25}
    monkeypatch.setattr(dp, "_cpacs_planform", lambda path: plan)
    rec = dp._wing_from_cpacs_file("/tmp/geometry_end_modified.xml")
    assert rec["Aircraft.Wing.AREA"] == 130.0 and rec["_source"] == "cpacs file /tmp/geometry_end_modified.xml"
    # A real measurement is never replaced by the file (B277 as before).
    assert dp._wing_from_cpacs_file("/tmp/other.xml") is rec

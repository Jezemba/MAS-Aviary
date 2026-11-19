"""B372: the chain carry-forward across the V1 / V5 changes (Jessica's hard requirement, 2026-11-18).

Every parameter an iteration changes must be exactly what the next iteration starts from -- the geometry (wing, tails,
fuselage) and its CPACS file, the engine cycle inputs (T4s included), the carried sized mass and engine (deck and
scale), and the design.json / design_flown.json / carry_over values the next link's prompt and pre-init read -- in
every structure, for a link that flew and for one that did not (B268).

Link 1 runs through the REAL middleware and call_watchdog (the B361 harness, tests/test_b361_end_to_end_threaded_link)
with a design change: the geometry agent reshapes the wing to AR 10 (set_high_level_parameters; the fake tigl answers
with measured before / after and exports the reshaped geometry) and the propulsion agent sets T4_MAX 2,900 K and BPR
5.5. The runner's own hand-over functions (scripts/stat_batch_runner.py: read_end_state_design,
carry_geometry_forward, design_space_of, last_flown_of, write_carry_over, setup_session_with_params) then start link 2,
and link 2's starting state -- what the agents are told AND what the servers and the data plane hold -- is compared
with link 1's flown end state, field by field. Sequential, orchestrated and networked link 1s, and graph pre-init.

The real validation runs are checked too (test_real_validation_*): S_SP and S_IF, link 1 -> link 2 (ws5 / ws3 copies,
read-only, extracted into tests/fixtures/v1a_validation1_records.json), where link 2 applied AR 10.0.

No network, no API, no MCP servers.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
from pathlib import Path

import pytest

import src.tools.data_plane as dp
import tests.test_b361_end_to_end_threaded_link as h
from src.coordination import prompt_assembly
from src.coordination.design_state import DesignState
from src.tools import coupling_contract as cc
from src.tools import knowledge_base as kbm
from src.tools import work_claims
from src.tools.agent_context import agent_scope

COMBO = "mdo_f25_sequential_staged_pipeline"
NEW_AR = 10.0
NEW_ENGINE = dict(h.ENGINE_INPUTS, T4_MAX=2900.0, **{"splitter.BPR": 5.5})
GEOMETRY_KEYS = ("Aircraft.Wing.AREA", "Aircraft.Wing.ASPECT_RATIO", "Aircraft.Wing.SWEEP", "Aircraft.Wing.TAPER_RATIO",
                 "Aircraft.Fuselage.LENGTH", "Aircraft.Fuselage.MAX_HEIGHT", "Aircraft.Fuselage.MAX_WIDTH")


class MorphingServers(h.FakeServers):
    """The B361 fake servers plus a tigl that really reshapes: the CPACS text holds the wing, set_high_level_parameters
    answers with measured before / after, export writes the current wing, open reads it back; SU2 is configured for
    the wing it is given; get_results reports the parameters the session holds (read_end_state_design)."""

    _MARK = "<!--avion-test-wing "

    def __init__(self, root: Path):
        super().__init__(root)
        self.wing = dict(self.replies["get_high_level_parameters"]["Wing1"]["parameters"])
        self.cpacs.write_text(self.cpacs_text(self.wing))
        self.n_sessions = 0

    def cpacs_text(self, wing) -> str:
        return f"<cpacs>{self._MARK}{json.dumps(wing, sort_keys=True)}--></cpacs>\n"

    def wing_of(self, path) -> dict:
        text = Path(path).read_text()
        return json.loads(text.split(self._MARK, 1)[1].split("-->", 1)[0])

    def _measured(self) -> dict:
        w = self.wing
        return {"span": w["span"], "reference_area": w["area"], "aspect_ratio": w["aspect_ratio"],
                "sweep_deg": w["sweep"], "taper_ratio": w["taper_ratio"]}

    def _answer(self, tool, kw):
        if tool == "open_cpacs":
            self.wing = self.wing_of(kw["source"])
            self.n_sessions += 1
            out = self.reply(tool)
            out["session_id"] = f"tigl-{self.n_sessions}"
            out["cpacs_metadata"]["file_name"] = kw["source"]
            return out
        if tool == "get_high_level_parameters" and kw["component_uid"] == "Wing1":
            out = self.reply(tool)["Wing1"]
            out["parameters"] = dict(self.wing)
            return out
        if tool == "set_high_level_parameters":
            before = self._measured()
            up = kw.get("updates") or {}
            area = float(up.get("area", self.wing["area"]))
            ar = float(up.get("aspect_ratio", self.wing["aspect_ratio"]))
            self.wing.update(area=area, aspect_ratio=ar, span=math.sqrt(area * ar),
                             sweep=float(up.get("sweep", self.wing["sweep"])))
            return {"component_uid": kw["component_uid"], "new_parameters": dict(self.wing), "warnings": [],
                    "geometry_morph": {"rebuilt": True, "before": before, "after": self._measured(),
                                       "targets_met": True}}
        if tool == "export_cpacs":
            dst = Path(kw["output_path"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(self.cpacs_text(self.wing))
            out = self.reply(tool)
            out["cpacs_file_path"] = str(dst)
            return out
        if tool == "create_su2_session":
            out = super()._answer(tool, kw)
            out["references"] = dict(out.get("references") or {}, REF_AREA=self.wing["area"])
            out["drag_floor"] = dict(out.get("drag_floor") or {}, aspect_ratio=self.wing["aspect_ratio"])
            return out
        if tool == "get_results":
            out = super()._answer(tool, kw)
            out["design_parameters"] = {"aircraft_params": dict(self.held.get(kw["session_id"], {}))}
            return out
        return super()._answer(tool, kw)


TOOLS = dict(h.TOOL_SERVER, set_high_level_parameters="tigl")
h.DECLARED.setdefault("set_high_level_parameters", ["session_id", "component_uid", "updates"])


def _wrap(servers):
    from src.tools.type_coercion import apply_coercion_to_tools

    return {t.name: t for t in apply_coercion_to_tools([h.FakeMCPTool(servers, n) for n in TOOLS])}


def _start_link(servers, link_dir: Path, link: int, params: dict, seed=None):
    """What the runner does at a link boundary: fresh data plane, the link's KB, its aviary session and mission."""
    from scripts.stat_batch_runner import setup_session_with_params

    dp.reset_design_state()
    dp.init_data_plane(dp._design_state, TOOLS)
    link_dir.mkdir(parents=True, exist_ok=True)
    kbm.configure_run(str(link_dir / "knowledge_base.jsonl"), link, 1, seed)
    work_claims.reset()
    setup = setup_session_with_params(servers.tools, params)
    servers.aviary_sid = setup["session_id"]
    dp.register_presession("aviary", servers.aviary_sid)
    return setup


@pytest.fixture
def chain(tmp_path, monkeypatch):
    import sys

    import src.config.canonical as canonical_module
    from src.config.canonical import load_canonical

    servers = MorphingServers(tmp_path)
    monkeypatch.setenv("AVION_DECKS_DIR", str(servers.decks))
    original = canonical_module.load_canonical
    patched = copy.deepcopy(original())
    patched["baseline"]["engine_deck_md5"] = hashlib.md5((servers.decks / h.DECK_NAME).read_bytes()).hexdigest()
    for module in list(sys.modules.values()):
        if getattr(module, "load_canonical", None) is original:
            monkeypatch.setattr(module, "load_canonical", lambda path=None: patched if path is None else original(path))
    for var in ("AVION_ENGINE_DECK", "AVION_CPACS_TAILS", "AVION_ENFORCE_CLAIMS", "AVION_COARSE_MESH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(dp, "_registered_tools", {})
    dp.init_data_plane(DesignState(), TOOLS)
    servers.tools = _wrap(servers)
    servers.out = tmp_path / "stat_results"
    servers.link_dir = [servers.out / f"repeat_{i:03d}" / COMBO for i in range(2)]
    anchor = {"Aircraft.Wing.AREA": 122.8287, "Aircraft.Wing.ASPECT_RATIO": 9.3632, "Aircraft.Wing.SWEEP": 21.6502,
              "Aircraft.Wing.TAPER_RATIO": 0.2462, "Aircraft.Fuselage.LENGTH": 37.6446,
              "Aircraft.Fuselage.MAX_HEIGHT": 4.1437, "Aircraft.Fuselage.MAX_WIDTH": 3.9518,
              "Aircraft.Engine.SCALE_FACTOR": 1.0}
    _start_link(servers, servers.link_dir[0], 0, anchor)
    _ = load_canonical
    yield servers
    work_claims.reset()
    from src.tools import duplicate_guard
    duplicate_guard.register_blackboard(None)
    dp.reset_design_state()
    dp.init_data_plane(DesignState(), {})
    kbm.configure_run(None, 0, 1, None)


# -- link 1 with a design change, in each structure ------------------------------------------------------------------

def geometry_reshaped(s):
    opened = h.call(s, "open_cpacs", source_type="path", source=str(s.cpacs))
    sid = opened["session_id"]
    for uid in ("Wing1", "Fuselage1"):
        h.call(s, "get_high_level_parameters", session_id=sid, component_uid=uid)
    morph = h.call(s, "set_high_level_parameters", session_id=sid, component_uid="Wing1",
                   updates={"aspect_ratio": NEW_AR})
    assert morph["geometry_morph"]["after"]["aspect_ratio"] == NEW_AR, morph
    s.exported = str(s.root / "export" / f"reshaped_{sid}.xml")
    assert h.call(s, "export_cpacs", session_id=sid, output_path=s.exported).get("status") == "success"
    mesh = h.call(s, "generate_volume_mesh", session_id=sid, output_format="su2")
    assert "error_code" not in mesh, mesh


def propulsion_changed(s):
    return h.propulsion(s, NEW_ENGINE)


def fly_link1(s, structure: str):
    if structure == "sequential":
        for agent, stage in (("geometry", geometry_reshaped), ("aero", h.aero), ("structures", h.structures),
                             ("propulsion", propulsion_changed), ("mission", h.mission)):
            h.run_as(agent, stage, s)
    elif structure == "orchestrated":
        h.run_as("geometry_worker", geometry_reshaped, s)
        h.run_as("structures_worker", h.structures, s)
        h.run_as("mission_worker", lambda x: h.call(x, "set_aircraft_parameters", session_id=x.aviary_sid,
                                                    parameters={}), s)
        h.run_as("aero_worker", h.aero, s)
        h.run_as("propulsion_worker", propulsion_changed, s)
        h.run_as("mission_worker", h.mission, s)
    else:                                        # networked: three peers in parallel with the claim board
        from src.coordination.blackboard import Blackboard
        from src.tools import duplicate_guard

        board = Blackboard(claiming_mode="hard")
        board.seed_todos([(n, n) for n in ("geometry", "aero", "mass", "propulsion", "mission", "simulation")])
        duplicate_guard.register_blackboard(board)
        work_claims.register_blackboard(board)
        work_claims.set_structure("networked")
        exported, sized, errors = threading.Event(), threading.Event(), []

        def peer(name, todos, body):
            try:
                with agent_scope(name, name):
                    for todo in todos:
                        assert board.claim_todo(todo, name)[0]
                    body()
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append((name, exc))
                exported.set()
                sized.set()

        def p1():
            geometry_reshaped(s)
            exported.set()
            h.aero(s)

        def p2():
            assert exported.wait(120)
            h.structures(s)
            propulsion_changed(s)
            sized.set()

        def p3():
            assert sized.wait(300)
            h.mission(s)

        threads = [threading.Thread(target=peer, args=a) for a in (("peer_1", ("geometry", "aero"), p1),
                                                                   ("peer_2", ("mass", "propulsion"), p2),
                                                                   ("peer_3", ("mission", "simulation"), p3))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(600)
        assert not errors, errors
    assert s.sim.get("success") is True, s.sim
    s.link1_flight = copy.deepcopy(s.flights[-1])
    s.link1_engine = {k: v for k, v in s.cycle.get(s.cycle_sid, {}).items()}
    s.link1_store = copy.deepcopy({k: v for k, v in dp._design_state.data_store.items()
                                   if k in ("mass_mtom_kg", "flown_cpacs_path", "geometry_wing")})


def hand_over(s, flew: bool = True) -> dict:
    """The runner at the end of link 1 (scripts/stat_batch_runner.py run loop, unchanged functions)."""
    # The link ran in the runner's child process; the runner reads the end state from the parent, where the link's
    # claim board does not exist.
    from src.tools import duplicate_guard

    work_claims.reset()
    duplicate_guard.register_blackboard(None)
    from scripts.stat_batch_runner import (carry_geometry_forward, design_space_of, last_flown_of,
                                           read_end_state_design, write_carry_over)

    d1, d2 = s.link_dir
    end_state = read_end_state_design(s.tools, s.aviary_sid)
    chain_cpacs = carry_geometry_forward(dp._design_state.data_store, d1) or str(s.cpacs)
    flown_dir = str(d1) if last_flown_of(d1) else ""
    assert bool(flown_dir) is flew
    write_carry_over(d2, 1, "VERDICT: CONTINUE", chain_cpacs, end_state, design_space_of(d1), previous_dir=str(d1),
                     flown_dir=flown_dir, flown_cpacs=chain_cpacs if flown_dir else None)
    s.end_state, s.chain_cpacs = end_state, chain_cpacs
    s.flown1 = json.loads((d1 / "design_flown.json").read_text()) if flew else None
    _start_link(s, d2, 1, end_state)
    return prompt_assembly.read_carry_over()


def _close(a, b):
    return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(b)))


def assert_link2_starts_where_link1_ended(s, carry, engine_already_set: bool = False):
    flown, flight = s.flown1, s.link1_flight
    start = carry["starting_design"]
    # 1. The geometry: the carried CPACS is the geometry the mission flew (AR 10), and what link 1's design says.
    assert Path(start["CPACS_FILE"]).read_text() == Path(s.exported).read_text()
    assert _close(s.wing_of(start["CPACS_FILE"])["aspect_ratio"], NEW_AR)
    assert start["geometry"] == flown["design_space"]["geometry"]                       # field by field
    assert _close(start["geometry"]["Wing1"]["aspect_ratio"], NEW_AR)
    # 2. The engine cycle inputs (T4s, BPR, PRs): as link 1 set and flew them.
    assert start["engine"] == flown["design_space"]["engine"]
    for k, v in NEW_ENGINE.items():
        assert _close(start["engine"][k], v), k
        assert _close(s.link1_engine[k], v), k
    # 3. The aviary session link 2 starts on holds what link 1 flew, for every settable design variable.
    held2 = s.held[s.aviary_sid]
    for key in GEOMETRY_KEYS + ("Aircraft.Engine.SCALE_FACTOR",):
        assert key in s.end_state, key
        assert _close(s.end_state[key], flight["held"][key]), key
        assert _close(held2[key], flight["held"][key]), key
        assert _close(carry["aviary_session_start"][key], flight["held"][key]), key
    assert _close(held2["Aircraft.Wing.ASPECT_RATIO"], NEW_AR)
    # 4. The carried sized mass and engine (deck and scale).
    assert carry["last_flown_design"] == flown or carry["last_flown_design"] == json.loads(json.dumps(flown))
    mass, why = dp.trim_mass_basis()
    assert _close(mass, flown["structures"]["mtom_kg"]) and "carried from the last flown iteration" in why
    deck, scale, _ = dp.carried_engine()
    assert deck == flown["engine_deck"]["file"] and Path(deck).name == Path(flight["deck"]).name
    assert _close(scale, flight["held"]["Aircraft.Engine.SCALE_FACTOR"])
    # 5. What the agents are told: the first stage's verbatim input and every orchestrated / networked agent's
    #    DESIGN_STATE carry the same values.
    seq_text = prompt_assembly.verbatim_stage_input("ROLE: GEOMETRY", [], carry, "geometry_engineer")
    state_text = prompt_assembly.starting_design_state("")
    for text in (seq_text, state_text):
        assert f"CPACS_FILE: {start['CPACS_FILE']}" in text
        assert f"Wing1: {prompt_assembly._fmt(flown['design_space']['geometry']['Wing1'])}" in text
        assert prompt_assembly._fmt(flown["design_space"]["engine"]) in text
    # 6. Link 2's engine: its first engine call gets link 1's engine when the agent sends nothing (B261 carried
    #    inputs); after graph pre-init the pyCycle session already holds them (pre-init set them).
    if engine_already_set:
        held = s.cycle[dp._design_state.sessions["pycycle"]]
        for k, v in NEW_ENGINE.items():
            assert _close(held[k], v), (k, held)
        return
    cyc = h.call(s, "create_cycle_model", cycle_type="turbofan", mode="design")
    h.call(s, "set_inputs", session_id=cyc["session_id"], values={})
    sent = s.sent("set_inputs")[-1]["values"]
    for k, v in NEW_ENGINE.items():
        assert _close(sent[k], v), (k, sent)


@pytest.mark.parametrize("structure", ["sequential", "orchestrated", "networked"])
def test_link2_starts_from_link1s_flown_end_state(chain, structure):
    fly_link1(chain, structure)
    carry = hand_over(chain)
    assert_link2_starts_where_link1_ended(chain, carry)


def test_graph_preinit_starts_link2_on_link1s_flown_design(chain):
    """Graph-routed link 2: pre-init opens the carried CPACS, seeds link 1's results on it (same geometry), re-sizes
    the engine on the carried mass and the CURRENT design. The seeded results must count as current (B372: the design
    identity of the carried file), and equal link 1's."""
    from src.coordination import graph_preinit

    fly_link1(chain, "sequential")
    carry = hand_over(chain)
    report = graph_preinit.prepare(carry)
    assert report.get("seed_source") == "last flown link's end-state", report
    assert "error" not in report, report
    store, flown = dp._design_state.data_store, chain.flown1
    assert _close(store["mass_mtom_kg"], flown["structures"]["mtom_kg"])
    assert _close(store["mass_wing_kg"], flown["structures"]["wing_mass_kg"])
    assert _close(store["aero_cl_cruise"], flown["aero"]["cl_cruise"])
    assert _close(store["geometry_wing"]["Aircraft.Wing.ASPECT_RATIO"], NEW_AR)
    for producer in ("su2", "mass"):
        assert store[f"_coupled_fp_{producer}"] == cc.current_design_fingerprint()
    assert cc.status(cc.AERO_SOLVE) == "ok" and cc.status(cc.WING_MASS) == "ok" and cc.status(cc.MTOM) == "ok"
    assert report.get("engine_current") is True, report
    # The re-sized engine is link 1's engine (its inputs) on link 1's design, sized at the CARRIED sized mass (A8:
    # iteration 1 sizes at the canonical 90,298.7 kg, iteration 2 at iteration 1's sized mass) with the carried scale.
    from src.tools import coupling

    link2_sizing = [kw["values"] for kw in chain.sent("set_inputs") if "Fn_DES" in (kw.get("values") or {})][-1]
    ask = chain.sent("get_cruise_point")[-1]
    assert _close(ask["mass_kg"], flown["structures"]["mtom_kg"])
    assert _close(ask["engine_scale"], flown["mission_applied"]["Aircraft.Engine.SCALE_FACTOR"])
    point = store["aviary_cruise_point"]
    assert point["_design_basis"] == dp.design_basis()
    assert _close(link2_sizing["Fn_DES"], coupling.fn_des_from_lift_to_drag(ask["mass_kg"], point["lift_to_drag"]))
    for k, v in NEW_ENGINE.items():
        assert _close(link2_sizing[k], v), k
    assert_link2_starts_where_link1_ended(chain, carry, engine_already_set=True)


def test_a_link_that_did_not_fly_carries_no_flown_basis(chain):
    """B268 side: link 1 reshaped and sized but never flew. The next link keeps the canonical basis (no flown design),
    and the starting design is that link's record, as before B372."""
    h.run_as("geometry", geometry_reshaped, chain)
    h.run_as("aero", h.aero, chain)
    h.run_as("structures", h.structures, chain)
    carry = hand_over(chain, flew=False)
    assert carry["last_flown_design"] is None
    mass, why = dp.trim_mass_basis()
    assert "canonical baseline" in why
    deck, scale, why_engine = dp.carried_engine()
    assert "baseline engine" in why_engine


# -- the real validation runs: S_SP and S_IF, link 1 -> link 2 ------------------------------------------------------

REAL = json.loads((Path(__file__).parent / "fixtures" / "v1a_validation1_records.json").read_text())["links"]


def _calls(link, tool):
    out = []
    for c in REAL[link]["tool_calls"]:
        if c.get("tool") == tool:
            out.append({k: (json.loads(v) if isinstance(v, str) and v[:1] in "{[" else v) for k, v in c.items()})
    return out


@pytest.mark.parametrize("combo", ["val_seq_staged", "val_seq_iter"])
def test_real_validation_link2_started_from_link1s_flown_end_state(combo):
    r0, r1 = REAL[f"{combo}/repeat_000"], REAL[f"{combo}/repeat_001"]
    flown, carry = r0["design_flown.json"], r1["carry_over.json"]
    start = carry["starting_design"]
    # geometry, engine inputs, mission scale: link 1's flown record, field by field
    assert start["geometry"] == flown["design_space"]["geometry"]
    assert start["engine"] == flown["design_space"]["engine"]
    assert start["CPACS_FILE"].endswith(carry["last_flown_dir"] + "/geometry_end.xml")
    assert f"/{combo}/repeat_000/" in start["CPACS_FILE"]
    assert carry["last_flown_geometry"] == start["CPACS_FILE"]
    assert carry["last_flown_design"]["structures"]["mtom_kg"] == flown["structures"]["mtom_kg"]
    applied = flown["mission_applied"]
    for key, value in carry["aviary_session_start"].items():
        assert value == applied[key], key
    # link 2 as it ran: its geometry agent opened the carried CPACS and applied AR 10.0 to it; tigl measured the wing
    # it started from (the morph's before) -- exactly link 1's flown wing, every planform value.
    opened = _calls(f"{combo}/repeat_001", "open_cpacs")
    assert opened[0]["args"]["source"] == start["CPACS_FILE"]
    w1 = flown["design_space"]["geometry"]["Wing1"]
    morph = _calls(f"{combo}/repeat_001", "set_high_level_parameters")
    assert morph and morph[0]["args"]["updates"] == {"aspect_ratio": 10.0}
    before = morph[0]["result"]["geometry_morph"]["before"]
    for tigl_key, design_key in (("reference_area", "area_m2"), ("aspect_ratio", "aspect_ratio"), ("span", "span_m"),
                                 ("sweep_deg", "sweep_quarter_chord_deg"), ("taper_ratio", "taper_ratio")):
        assert before[tigl_key] == w1[design_key], tigl_key
    # the carried sized mass is link 2's trim basis and the carried engine its cruise-point scale
    point = _calls(f"{combo}/repeat_001", "get_cruise_point")[0]["args"]
    assert point["mass_kg"] == flown["structures"]["mtom_kg"]
    assert point["engine_scale"] == applied["Aircraft.Engine.SCALE_FACTOR"]
    # link 2's first engine call carried link 1's cycle inputs (T4s included)
    first_set = _calls(f"{combo}/repeat_001", "set_inputs")[0]["args"]["values"]
    for k, v in flown["design_space"]["engine"].items():
        assert float(first_set[k]) == float(v), k


@pytest.mark.parametrize("combo", ["val_seq_staged", "val_seq_iter"])
def test_real_validation_measured_fuel_unchanged_by_the_digest(combo, tmp_path, monkeypatch):
    """The runner's measured fuel (B102) read with the B372 digest is the fuel each link reported: the run_simulation
    replies of the recorded links, recorded by the new KB, give the same fuel as get_results did."""
    monkeypatch.setattr(dp, "_design_state", DesignState())
    for link in ("repeat_000", "repeat_001"):
        path = tmp_path / link / "knowledge_base.jsonl"
        kbm.configure_run(str(path), 0, 1, None)
        dp._design_state.knowledge_base = None
        kb = kbm.get_kb()
        sims = _calls(f"{combo}/{link}", "run_simulation")
        results = _calls(f"{combo}/{link}", "get_results")
        for c in sims:
            kb.append(tool="run_simulation", server="aviary", agent="x", role="", status="success", inputs={},
                      outputs=c["result"])
        from scripts.stat_batch_runner import measured_fuel_from_kb

        fuel = measured_fuel_from_kb(path, 1)
        assert fuel == sims[-1]["result"]["summary"]["fuel_burned_kg"]
        assert any(r["result"].get("fuel_burned_kg") == fuel for r in results)
    kbm.configure_run(None, 0, 1, None)

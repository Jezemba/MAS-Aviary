"""B360 / B361 / B362 end to end: one complete link per structure through the REAL middleware and call_watchdog.

Every tool is a fake MCP tool object wrapped by the real loader path (type_coercion.apply_coercion_to_tools), so each
call runs exactly as in a study run: tool.forward -> bounded_forward -> call_watchdog.bounded (a pooled worker thread,
the caller's context copied) -> resolve_request -> guards -> call_watchdog.call -> fake server -> intercept_response.
The framework's own calls (the cruise-point set + get_cruise_point inside set_inputs / run_cycle, the B187 / B272
re-applies and the engine deck inside run_simulation) are issued by the data plane through the same registered tools,
so they run in watchdog threads of their own -- the situation B360 was invisible in (test_b340's stub World calls the
middleware in one thread).

The fake servers answer with the replies of the recorded coupled baseline flight (ws8
/data/Jessica/final_fix/baseline2/results/flights/B2_coupled_anchor_2, final code, 2026-11-18;
tests/fixtures/b361_baseline_flight_replies.json, extracted by tests/fixtures/b361_extract_baseline_flight_replies.py),
with the fields that depend on the request computed from it: aviary's design fingerprint follows the parameters the
session holds (the recorded fingerprint for the recorded design), the cruise point is evaluated at the requested mass
and engine scale, pyCycle's thrust is the Fn_DES it was given.

Links: sequential (one agent per stage, in turn), orchestrated (workers in another order, with a caller value the
framework replaces and a refused tail value), networked (three peers in parallel threads with the B86 claim board, the
propulsion peer's framework set landing on the mission peer's claimed TODO, and refusals racing a peer's good calls).
Each must size the engine (Fn_DES from aviary's cruise L/D at the carried sized mass) and fly the mission with the
measured tails and wing t/c, the engine scale and the deck. On bef8c16 (B360) no link sizes the engine.

No network, no API, no MCP servers: fake tools only.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import threading
from pathlib import Path

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import coupling, work_claims
from src.tools import knowledge_base as kbm
from src.tools.agent_context import agent_scope

FIXTURE = Path(__file__).parent / "fixtures" / "b361_baseline_flight_replies.json"
_RAW = FIXTURE.read_text()
REC = json.loads(_RAW)["recorded"]

SERVERS = {
    "aviary": ("create_session", "configure_mission", "set_aircraft_parameters", "get_cruise_point", "set_engine_deck",
               "run_simulation", "get_results", "get_trajectory"),
    "tigl": ("open_cpacs", "get_high_level_parameters", "export_cpacs", "generate_volume_mesh", "close_cpacs"),
    "su2": ("create_su2_session", "run_su2_solver", "close_su2_session"),
    "mass": ("estimate_mass",),
    "pycycle": ("create_cycle_model", "set_inputs", "run_cycle", "get_outputs", "generate_engine_deck",
                "close_cycle_model"),
}
TOOL_SERVER = {tool: server for server, tools in SERVERS.items() for tool in tools}
# The arguments each server tool declares: what the recorded flight sent (after injection) plus get_outputs' names.
DECLARED = dict(REC["resolved_inputs"], get_outputs=["names", "session_id"])
# aviary-mcp's design fingerprint covers the aerodynamic inputs (get_cruise_point aero_details.fingerprint_covers):
# not the wing mass, not the engine scale (the recorded flight's three sets with and without them share one).
NOT_FINGERPRINTED = {"Aircraft.Wing.MASS", "Aircraft.Engine.SCALE_FACTOR", "Aircraft.Wing.SPAN"}
ENGINE_INPUTS = {"splitter.BPR": 5.105, "T4_MAX": 2857.0, "T4_TAKEOFF": 3540.0, "T4_CLIMB": 3090.0,
                 "fan.PR": 1.685, "lpc.PR": 1.935, "hpc.PR": 9.369}
DECK_NAME = REC["canonical_baseline"]["engine_deck_file"]
TAIL_KEYS = [k for k in REC["geometry_tails"] if not k.startswith("_")]
TC_KEY = "Aircraft.Wing.THICKNESS_TO_CHORD"


def _close(a, b, rel=1e-9):
    return abs(float(a) - float(b)) <= rel * max(1.0, abs(float(b)))


class FakeServers:
    """The five MCP servers, answering with the recorded flight's replies (see the module docstring)."""

    def __init__(self, root: Path):
        self.root = root
        self.decks = root / "decks"
        self.su2_dir = root / "su2_session"
        for d in (self.decks, self.su2_dir):
            d.mkdir(parents=True, exist_ok=True)
        (self.decks / DECK_NAME).write_bytes(b"recorded baseline deck stand-in\n")
        raw = (_RAW.replace("/data/Jessica/final_fix/baseline2/slot11/decks", str(self.decks))
               .replace("/data/Jessica/final_fix/baseline2/tmp/su2_session_18wndw7i", str(self.su2_dir)))
        self.replies = json.loads(raw)["replies"]
        self.cpacs = root / "D150_simple.xml"
        self.cpacs.write_text("<cpacs><header><name>D150 stand-in</name></header></cpacs>\n")
        self.lock = threading.Lock()
        self.held: dict[str, dict] = {}
        self.deck_on: dict[str, str] = {}
        self.cycle: dict[str, dict] = {}
        self.calls: list[tuple[str, dict, str]] = []
        self.flights: list[dict] = []
        self.recorded_aero = {k: v for k, v in REC["flown_parameters"].items() if k not in NOT_FINGERPRINTED}

    # -- aviary's fingerprint of the design a session holds ---------------------------------------------
    def fingerprint(self, sid) -> str:
        held = {k: v for k, v in self.held.get(sid, {}).items() if k not in NOT_FINGERPRINTED}
        if set(held) == set(self.recorded_aero) and all(_close(held[k], v) for k, v in self.recorded_aero.items()):
            return REC["aviary_design_fingerprint"]
        return "fp-" + hashlib.md5(json.dumps(sorted(held.items()), default=str).encode()).hexdigest()[:16]

    def reply(self, tool):
        return copy.deepcopy(self.replies[tool])

    def handle(self, tool: str, kw: dict) -> dict:
        with self.lock:
            self.calls.append((tool, copy.deepcopy(kw), threading.current_thread().name))
            return self._answer(tool, kw)

    def _answer(self, tool, kw):
        r = self.replies
        if tool == "create_session":
            out = self.reply(tool)
            self.held[out["session_id"]] = dict(kw.get("initial_parameters") or {})
            out["design_fingerprint"] = self.fingerprint(out["session_id"])
            return out
        if tool == "configure_mission":
            out = self.reply(tool)
            out["design_fingerprint"] = self.fingerprint(kw["session_id"])
            return out
        if tool == "set_aircraft_parameters":
            sid, params = kw["session_id"], dict(kw.get("parameters") or {})
            held = self.held.setdefault(sid, {})
            applied = [{"name": k, "old_value": held.get(k), "new_value": v} for k, v in params.items()]
            held.update(params)
            out = self.reply(tool)
            out.update(session_id=sid, applied=applied, design_fingerprint=self.fingerprint(sid))
            return out
        if tool == "get_cruise_point":
            sid = kw["session_id"]
            out = self.reply(tool)
            fp = self.fingerprint(sid)
            point = out["cruise_point"]
            if fp != REC["aviary_design_fingerprint"]:           # another design: another (deterministic) L/D
                point["lift_to_drag"] = 15.0 + int(hashlib.md5(fp.encode()).hexdigest()[:4], 16) / 65535.0
                point["cd"] = point["cl"] / point["lift_to_drag"]
            point.update(mass_kg=kw["mass_kg"], mach=kw["mach"], altitude_ft=kw["altitude_ft"],
                         engine_scale_used=kw.get("engine_scale"), design_fingerprint=fp)
            out.update(session_id=sid, design_fingerprint=fp)
            return out
        if tool == "set_engine_deck":
            self.deck_on[kw["session_id"]] = kw["deck_file"]
            out = self.reply(tool)
            out["engine"]["path"] = kw["deck_file"]
            out["design_fingerprint"] = self.fingerprint(kw["session_id"])
            return out
        if tool == "run_simulation":
            sid = kw["session_id"]
            fp = self.fingerprint(sid)
            self.flights.append({"session": sid, "held": dict(self.held.get(sid, {})), "deck": self.deck_on.get(sid),
                                 "fingerprint": fp, "thread": threading.current_thread().name})
            out = self.reply(tool)
            out.update(session_id=sid, flown_design_fingerprint=fp, design_fingerprint=fp)
            return out
        if tool in ("get_results", "get_trajectory"):
            out = self.reply(tool)
            fp = self.flights[-1]["fingerprint"] if self.flights else self.fingerprint(kw["session_id"])
            out.update(flown_design_fingerprint=fp, design_fingerprint=self.fingerprint(kw["session_id"]))
            return out
        if tool == "get_high_level_parameters":
            return copy.deepcopy(r["get_high_level_parameters"][kw["component_uid"]])
        if tool == "export_cpacs":
            dst = Path(kw["output_path"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.cpacs, dst)
            out = self.reply(tool)
            out["cpacs_file_path"] = str(dst)
            return out
        if tool == "generate_volume_mesh":
            out = self.reply(tool)
            out["mesh_base64"] = "U1UyIE1FU0ggU1RBTkQtSU4K" * 20000          # large enough to go behind a ref
            return out
        if tool == "estimate_mass":
            out = self.reply(tool)
            out["cpacs_file_path"] = kw["cpacs_file_path"]
            return out
        if tool == "set_inputs":
            self.cycle.setdefault(kw["session_id"], {}).update(kw.get("values") or {})
            return {"updated": list((kw.get("values") or {}).keys()), "skipped": []}
        if tool == "run_cycle":
            out = self.reply(tool)
            out["outputs"]["perf.Fn"] = float(self.cycle.get(kw["session_id"], {}).get("Fn_DES", 5900.0))
            return out
        if tool == "get_outputs":
            return {"values": {"perf.Fn": float(self.cycle.get(kw["session_id"], {}).get("Fn_DES", 5900.0))}}
        if tool in r:
            return self.reply(tool)
        return {"success": True}

    def sent(self, tool):
        return [kw for t, kw, _ in self.calls if t == tool]


class FakeMCPTool:
    """An MCP tool as mcpadapt hands it to the loader: name, inputs, forward -> JSON text."""

    output_type = "string"

    def __init__(self, servers: FakeServers, name: str):
        self.servers, self.name = servers, name
        self.description = f"{name} (fake MCP tool, recorded baseline replies)"
        self.inputs = {k: {"type": "any", "description": k, "nullable": True} for k in DECLARED[name]}

    def forward(self, **kw):
        return json.dumps(self.servers.handle(self.name, kw))


@pytest.fixture
def link(tmp_path, monkeypatch):
    """A fresh link: data plane, knowledge base, canonical baseline deck, wrapped fake tools, the runner's session."""
    from src.config.canonical import load_canonical
    from src.tools.type_coercion import apply_coercion_to_tools

    servers = FakeServers(tmp_path)
    monkeypatch.setenv("AVION_DECKS_DIR", str(servers.decks))
    # The canonical baseline deck is named by file (AVION_DECKS_DIR) and checked by md5: the stand-in deck's md5.
    import sys

    import src.config.canonical as canonical_module

    original = canonical_module.load_canonical
    patched = copy.deepcopy(original())
    patched["baseline"]["engine_deck_md5"] = hashlib.md5((servers.decks / DECK_NAME).read_bytes()).hexdigest()

    def load_canonical_for_test(path=None):
        return patched if path is None else original(path)

    for module in list(sys.modules.values()):
        if getattr(module, "load_canonical", None) is original:
            monkeypatch.setattr(module, "load_canonical", load_canonical_for_test)
    for var in ("AVION_ENGINE_DECK", "AVION_CPACS_TAILS", "AVION_ENFORCE_CLAIMS", "AVION_COARSE_MESH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(dp, "_registered_tools", {})
    dp.init_data_plane(DesignState(), TOOL_SERVER)
    dp.reset_design_state()
    kbm.configure_run(str(tmp_path / "link" / "knowledge_base.jsonl"), 1, 1, None)
    work_claims.reset()
    tools = {t.name: t for t in apply_coercion_to_tools([FakeMCPTool(servers, n) for n in TOOL_SERVER])}
    servers.tools = tools
    # The runner's pre-hook (stat_batch_runner.setup_session_with_params): the study's aviary session and mission.
    mission = load_canonical()["mission"]
    created = json.loads(tools["create_session"].forward(initial_parameters={
        "Aircraft.Wing.AREA": 122.78, "Aircraft.Wing.ASPECT_RATIO": 9.37, "Aircraft.Wing.SWEEP": 21.65,
        "Aircraft.Wing.TAPER_RATIO": 0.246, "Aircraft.Fuselage.LENGTH": 37.68, "Aircraft.Fuselage.MAX_HEIGHT": 4.15,
        "Aircraft.Fuselage.MAX_WIDTH": 3.96, "Aircraft.Engine.SCALE_FACTOR": 1.0}))
    servers.aviary_sid = created["session_id"]
    tools["configure_mission"].forward(session_id=servers.aviary_sid, range_nmi=mission["range_nmi"],
                                       num_passengers=mission["num_passengers"], cruise_mach=mission["cruise_mach"],
                                       cruise_altitude_ft=mission["cruise_altitude_ft"],
                                       optimizer_max_iter=mission.get("optimizer_max_iter", 200))
    dp.register_presession("aviary", servers.aviary_sid)
    yield servers
    work_claims.reset()
    from src.tools import duplicate_guard
    duplicate_guard.register_blackboard(None)
    dp.reset_design_state()
    dp.init_data_plane(DesignState(), {})
    kbm.configure_run(None, 0, 1, None)


def call(servers, tool, **kw) -> dict:
    out = servers.tools[tool].forward(**kw)
    return json.loads(out) if isinstance(out, str) else out


def run_as(agent: str, fn, *args):
    """An agent's run: its own thread, its identity set around it (as the strategies do), errors re-raised."""
    box = {}

    def body():
        try:
            with agent_scope(agent, agent):
                box["out"] = fn(*args)
        except BaseException as exc:  # pragma: no cover - re-raised in the test thread
            box["exc"] = exc

    t = threading.Thread(target=body, name=f"agent-{agent}")
    t.start()
    t.join(600)
    assert not t.is_alive(), f"{agent} did not finish"
    if "exc" in box:
        raise box["exc"]
    return box.get("out")


# -- the stages, as the recorded flight called them (code/a16/baseline_driver.py fly) -------------------------------

def geometry(s):
    opened = call(s, "open_cpacs", source_type="path", source=str(s.cpacs))
    sid = opened["session_id"]
    for uid in ("Wing1", "Fuselage1"):
        assert call(s, "get_high_level_parameters", session_id=sid, component_uid=uid).get("measured") is True
    s.exported = str(s.root / "link" / "cpacs" / "anchor_unchanged.xml")
    out = call(s, "export_cpacs", session_id=sid, output_path=s.exported)
    assert out.get("status") == "success", out
    mesh = call(s, "generate_volume_mesh", session_id=sid, output_format="su2")
    assert "error" not in mesh and "error_code" not in mesh, mesh


def aero(s):
    from src.config.canonical import load_canonical

    mission = load_canonical()["mission"]
    su2 = call(s, "create_su2_session", cpacs_file_path=s.exported, cruise_mach=mission["cruise_mach"],
               cruise_altitude_ft=mission["cruise_altitude_ft"])
    assert su2.get("configured") is True, su2
    solve = call(s, "run_su2_solver", session_id=su2["session_id"], max_runtime_seconds=3600)
    assert solve.get("success") is True, solve


def structures(s):
    out = call(s, "estimate_mass", cpacs_file_path=s.exported)
    assert out.get("status") == "success", out


def propulsion(s, values=None):
    cyc = call(s, "create_cycle_model", cycle_type="turbofan", mode="design")
    s.cycle_sid = cyc["session_id"]
    s.set_inputs_reply = call(s, "set_inputs", session_id=s.cycle_sid, values=dict(values or ENGINE_INPUTS))
    s.run_cycle_reply = call(s, "run_cycle", session_id=s.cycle_sid)
    return s.run_cycle_reply


def mission(s):
    s.set_reply = call(s, "set_aircraft_parameters", session_id=s.aviary_sid, parameters={})
    s.sim = call(s, "run_simulation", session_id=s.aviary_sid, timeout_seconds=600)
    s.results = call(s, "get_results", session_id=s.aviary_sid)
    return s.sim


# -- what every link must show ----------------------------------------------------------------------------------------

def assert_engine_sized_and_mission_flown(s):
    point = REC["cruise_point"]
    mass = REC["canonical_baseline"]["sized_mtom_kg"]
    # The framework asked aviary for the cruise point of the CURRENT design at the carried sized mass and engine.
    asks = s.sent("get_cruise_point")
    assert asks, "the framework never asked aviary for the cruise point"
    assert _close(asks[-1]["mass_kg"], mass) and _close(asks[-1]["engine_scale"],
                                                          REC["canonical_baseline"]["engine_scale_factor"])
    stored = dp._design_state.data_store.get("aviary_cruise_point") or {}
    assert stored.get("_design_basis") == dp.design_basis(), "B360: the point never got its design basis"
    # The engine is sized: pyCycle got Fn_DES from aviary's L/D (the recorded 7,055.20 lbf), the cycle ran.
    fn_des = coupling.fn_des_from_lift_to_drag(mass, point["lift_to_drag"])
    assert _close(fn_des, REC["fn_des_lbf"])
    sized = [kw["values"]["Fn_DES"] for kw in s.sent("set_inputs") if "Fn_DES" in (kw.get("values") or {})]
    assert sized and _close(sized[-1], fn_des), f"no Fn_DES reached pyCycle: {s.sent('set_inputs')}"
    assert s.sent("run_cycle"), "run_cycle never reached pyCycle (refused DESIGN_THRUST_NOT_AVAILABLE?)"
    assert dp.engine_is_current()
    # The mission flew with the measured tails and wing t/c, the engine's scale and the designed deck.
    assert s.sim.get("success") is True and s.sim.get("error_code") is None, s.sim
    flight = s.flights[-1]
    held = flight["held"]
    for key in TAIL_KEYS:
        assert _close(held[key], REC["geometry_tails"][key]), key
    assert _close(held[TC_KEY], REC["geometry_wing_tc"][TC_KEY])
    scale = fn_des / float(s.replies["generate_engine_deck"]["reference_fn_des_lbf"])
    assert _close(held["Aircraft.Engine.SCALE_FACTOR"], scale)
    assert _close(scale, REC["engine_scale"])
    assert flight["deck"] and Path(flight["deck"]).name == DECK_NAME
    assert flight["fingerprint"] == REC["aviary_design_fingerprint"]     # the recorded flight's design, exactly
    assert _close(s.results["fuel_burned_kg"], REC["fuel_burned_kg"])
    # The framework's own calls ran in watchdog threads, never in the agent's.
    framework_threads = {th for t, _, th in s.calls if t in ("get_cruise_point", "set_engine_deck")}
    assert framework_threads and all(th.startswith("avion-tool-call") for th in framework_threads)


# -- the three structures ---------------------------------------------------------------------------------------------

def test_b361_sequential_link_sizes_the_engine_and_flies(link):
    for agent, stage in (("geometry", geometry), ("aero", aero), ("structures", structures),
                         ("propulsion", propulsion), ("mission", mission)):
        run_as(agent, stage, link)
    assert link.run_cycle_reply.get("error_code") is None, link.run_cycle_reply
    assert_engine_sized_and_mission_flown(link)


def test_b361_orchestrated_link_other_order_and_refusals_reach_the_caller(link):
    """Workers in another order (mass before aero, the mission applied before propulsion), a caller's Fn_DES the
    framework replaces (the note reaches THAT reply, not the framework's nested set), a refused tail value."""
    def propulsion_with_fn(s):
        cyc = call(s, "create_cycle_model", cycle_type="turbofan", mode="design")
        s.cycle_sid = cyc["session_id"]
        values = dict(ENGINE_INPUTS, Fn_DES=6000.0, **{"fan.eff": 0.90})      # both are the framework's
        s.set_inputs_reply = call(s, "set_inputs", session_id=s.cycle_sid, values=values)
        s.run_cycle_reply = call(s, "run_cycle", session_id=s.cycle_sid)

    def mission_first_set(s):
        bad = call(s, "set_aircraft_parameters", session_id=s.aviary_sid,
                   parameters={"Aircraft.HorizontalTail.AREA": 40.0})
        s.refused_tail = bad
        s.early_set = call(s, "set_aircraft_parameters", session_id=s.aviary_sid, parameters={})

    run_as("geometry_worker", geometry, link)
    run_as("structures_worker", structures, link)
    run_as("mission_worker", mission_first_set, link)
    run_as("aero_worker", aero, link)
    run_as("propulsion_worker", propulsion_with_fn, link)
    run_as("mission_worker", mission, link)
    # The tail value was refused to the caller, nothing reached aviary for it.
    assert link.refused_tail.get("error_code") == "TAIL_FROM_GEOMETRY", link.refused_tail
    assert all(not _close((kw.get("parameters") or {}).get("Aircraft.HorizontalTail.AREA", -1), 40.0)
               for kw in link.sent("set_aircraft_parameters"))
    assert link.early_set.get("success") is True
    # B361: what the framework replaced is told to the caller. The fixed fan.eff note was written before the
    # framework's nested set_aircraft_parameters, whose post-hook popped it from the shared store (lost).
    applied = (link.set_inputs_reply.get("framework_applied") or {}).get("overridden") or {}
    assert applied.get("Fn_DES", {}).get("asked") == 6000.0, link.set_inputs_reply
    assert applied.get("fan.eff", {}).get("asked") == 0.90, link.set_inputs_reply
    assert "framework_applied" not in link.early_set     # nobody else's notes on the mission worker's reply
    assert link.run_cycle_reply.get("error_code") is None, link.run_cycle_reply
    assert_engine_sized_and_mission_flown(link)


def test_b361_networked_link_parallel_peers_with_claims(link):
    """Three peers at once with the B86 claim board. The propulsion peer's set_inputs makes the framework apply the
    design to aviary -- a set_aircraft_parameters, the mission peer's claimed TODO (B362: was refused, so no cruise
    point and no engine). Refusals race a peer's good calls and reach only their caller."""
    from src.coordination.blackboard import Blackboard
    from src.tools import duplicate_guard

    board = Blackboard(claiming_mode="hard")
    board.seed_todos([(n, n) for n in ("geometry", "aero", "mass", "propulsion", "mission", "simulation")])
    duplicate_guard.register_blackboard(board)
    work_claims.register_blackboard(board)
    work_claims.set_structure("networked")
    exported, sized, both = threading.Event(), threading.Event(), threading.Barrier(2, timeout=120)
    claimed = threading.Barrier(3, timeout=120)
    out = {}

    def peer_geometry_aero(s):
        for todo in ("geometry", "aero"):
            assert board.claim_todo(todo, "peer_1")[0]
        claimed.wait()
        geometry(s)
        exported.set()
        aero(s)
        # Not its work: the propulsion TODO is peer_2's (a refusal for peer_1 only). After the sizing, so the cruise
        # point is valid and the pre-hook issues no framework call: the refusal is all this call does.
        assert sized.wait(300)
        out["trespass"] = call(s, "set_inputs", session_id=s.cycle_sid, values=dict(ENGINE_INPUTS))

    def peer_mass_propulsion(s):
        for todo in ("mass", "propulsion"):
            assert board.claim_todo(todo, "peer_2")[0]
        claimed.wait()
        assert exported.wait(120)
        structures(s)
        cyc = call(s, "create_cycle_model", cycle_type="turbofan", mode="design")
        s.cycle_sid = cyc["session_id"]
        both.wait()                       # at the same moment as peer_3's own set_aircraft_parameters
        out["bad_key"] = call(s, "set_inputs", session_id=s.cycle_sid, values={"balance.lpt_PR": 3.0})
        s.set_inputs_reply = call(s, "set_inputs", session_id=s.cycle_sid, values=dict(ENGINE_INPUTS))
        s.run_cycle_reply = call(s, "run_cycle", session_id=s.cycle_sid)
        sized.set()

    def peer_mission(s):
        for todo in ("mission", "simulation"):
            assert board.claim_todo(todo, "peer_3")[0]
        claimed.wait()
        assert exported.wait(120)
        both.wait()
        out["peer3_set"] = call(s, "set_aircraft_parameters", session_id=s.aviary_sid, parameters={})
        assert sized.wait(300)
        mission(s)

    threads, errors = [], []
    for name, fn in (("peer_1", peer_geometry_aero), ("peer_2", peer_mass_propulsion), ("peer_3", peer_mission)):
        def body(fn=fn, name=name):
            try:
                with agent_scope(name, name):
                    fn(link)
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append((name, exc))
                exported.set()
                sized.set()
                both.abort()
                claimed.abort()
        threads.append(threading.Thread(target=body, name=f"agent-{name}"))
    for t in threads:
        t.start()
    for t in threads:
        t.join(600)
    assert not errors, errors
    assert out["trespass"].get("error_code") == work_claims.ERROR_CLAIMED_BY_OTHER, out["trespass"]
    assert out["bad_key"].get("error_code") == "UNKNOWN_CYCLE_KEY", out["bad_key"]
    assert out["peer3_set"].get("success") is True and out["peer3_set"].get("error_code") is None, out["peer3_set"]
    assert link.set_inputs_reply.get("error_code") is None, link.set_inputs_reply
    assert link.run_cycle_reply.get("error_code") is None, link.run_cycle_reply
    # The framework's set ran for peer_2 without taking peer_3's mission TODO.
    owners = {t.name: t.assigned_to for t in board.read_todos()}
    assert owners["mission"] == "peer_3" and owners["propulsion"] == "peer_2"
    assert_engine_sized_and_mission_flown(link)


def test_b361_three_peers_size_the_engine_at_once(link):
    """Three peers ask for the engine sizing at the same moment (set_inputs on the link's pycycle session): one framework
    set + ask at a time (B361 lock), every peer gets Fn_DES of the current design, the point is asked once."""
    run_as("geometry", geometry, link)
    run_as("aero", aero, link)
    run_as("structures", structures, link)
    cycle_sid = call(link, "create_cycle_model", cycle_type="turbofan", mode="design")["session_id"]
    gate = threading.Barrier(3, timeout=120)
    replies = {}

    def size(name):
        gate.wait()
        replies[name] = call(link, "set_inputs", session_id=cycle_sid, values=dict(ENGINE_INPUTS))

    threads = [threading.Thread(target=run_as, args=(n, size, n), name=f"start-{n}") for n in ("p1", "p2", "p3")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(600)
    fn_des = coupling.fn_des_from_lift_to_drag(REC["canonical_baseline"]["sized_mtom_kg"],
                                               REC["cruise_point"]["lift_to_drag"])
    assert len(replies) == 3 and all(r.get("error_code") is None for r in replies.values()), replies
    sent = [kw["values"].get("Fn_DES") for kw in link.sent("set_inputs")]
    assert len(sent) == 3 and all(v is not None and _close(v, fn_des) for v in sent), sent
    # The point was asked once for the design (the peers that waited reused it), not once per peer.
    assert len(link.sent("get_cruise_point")) == 1, link.sent("get_cruise_point")


# -- B372 (V1): the graph-routed links must REACH THE INTEGRATOR with the real-size replies ---------------------------
#
# The recorded run_simulation reply above is the real one (~4.5 kB: engine, climb_start, residuals, constraints,
# overridden outputs, then summary). B361's links flew it, but nothing here routed on what the knowledge base recorded:
# the graph router read the fuel from the capped record, never saw a flight, and validation 1's S_GR / O_GR looped
# mission <-> simulation until the budget (V1). These run the REAL graph handler / board graph on the KB the real
# middleware wrote.

class GraphAgent:
    """A graph node's agent: its stage, run against the wrapped fake tools, and its final text."""

    def __init__(self, name, servers, stage, text="DESIGN_STATE: done"):
        self.name, self.description, self.tools = name, f"ROLE: {name.upper()}", {}
        self.servers, self.stage, self.text, self.calls = servers, stage, text, []

    def run(self, context):
        self.calls.append(context)
        with agent_scope(self.name, self.name):
            self.stage(self.servers)
        return self.text


def _prepared(s):
    """What graph pre-init prepares before the SIMPLE walk: geometry, SU2, mass and the sized engine."""
    for agent, stage in (("geometry", geometry), ("aero", aero), ("structures", structures),
                         ("propulsion", propulsion)):
        run_as(agent, stage, s)


def _set_mission(s):
    s.set_reply = call(s, "set_aircraft_parameters", session_id=s.aviary_sid, parameters={})


def _fly(s):
    s.sim = call(s, "run_simulation", session_id=s.aviary_sid, timeout_seconds=600)
    s.results = call(s, "get_results", session_id=s.aviary_sid)


def _graph_agents(s):
    from src.coordination import graph_router as gr

    return {gr.MISSION: GraphAgent(gr.MISSION, s, _set_mission),
            gr.SIMULATION: GraphAgent(gr.SIMULATION, s, _fly),
            gr.REVIEW: GraphAgent(gr.REVIEW, s, lambda _s: None, "VERDICT: COMPLETE")}


def _assert_the_recorded_flight_is_seen(s):
    from src.coordination import graph_router as gr

    reply = s.replies["run_simulation"]
    assert len(json.dumps(reply)) > kbm._OUTPUTS_CAP_CHARS                      # the real size
    rec = [e for e in kbm.get_kb().entries if e["tool"] == "run_simulation"][-1]
    assert rec["result_digest"]["fuel_burned_kg"] == reply["summary"]["fuel_burned_kg"]
    assert gr._flown(rec) == reply["summary"]["fuel_burned_kg"]


def _graph_env(monkeypatch):
    from src.coordination import prompt_assembly

    monkeypatch.setattr(prompt_assembly, "read_carry_over", lambda: None)
    monkeypatch.setattr(prompt_assembly, "read_design_json_text", lambda: None)


def test_b372_sequential_graph_link_reaches_the_integrator(link, monkeypatch):
    from src.coordination import conditional_graph_handler as h_mod
    from src.coordination import graph_router as gr
    from src.coordination.execution_handler import Assignment

    _graph_env(monkeypatch)
    _prepared(link)
    handler = h_mod.ConditionalGraphHandler({"preinit": False})
    msgs = handler.execute([Assignment(agent_name=gr.MISSION, task="T")], _graph_agents(link), None)
    path = [(r["node"], r["rule"]) for r in handler.route_log]
    assert path == [(gr.MISSION, "flow"), (gr.SIMULATION, "flow"), (gr.REVIEW, "complete")], handler.route_log
    assert msgs[-1].metadata["graph_stop_reason"] == "complete"
    assert_engine_sized_and_mission_flown(link)
    _assert_the_recorded_flight_is_seen(link)


def test_b372_orchestrated_graph_link_reaches_the_integrator(link, monkeypatch):
    from types import SimpleNamespace

    from src.coordination import conditional_graph_handler as h_mod
    from src.coordination import graph_router as gr
    from src.coordination.execution_handler import Assignment

    _graph_env(monkeypatch)
    _prepared(link)
    meta = {"assigned_tasks": {}, "system_text": "", "inbox": {}, "assignments": [],
            "orchestrator_name": "orchestrator", "ask_orchestrator": lambda sender, message: None}
    agents = dict(_graph_agents(link), orchestrator=SimpleNamespace(tools={}))
    handler = h_mod.ConditionalGraphHandler({"preinit": False, "worker_input": "as_given", "decider": "orchestrator"})
    msgs = handler.execute([Assignment(agent_name=gr.MISSION, task="")], agents, None, action_metadata=meta)
    path = [(r["node"], r["rule"]) for r in handler.route_log]
    assert path == [(gr.MISSION, "flow"), (gr.SIMULATION, "flow"), (gr.REVIEW, "complete")], handler.route_log
    assert msgs[-1].metadata["graph_stop_reason"] == "complete"
    _assert_the_recorded_flight_is_seen(link)


def test_b372_networked_graph_board_completes_on_the_flight(link):
    """Networked graph: the board graph posts no 'did not fly' suggestion after the flight, and the integrator's
    COMPLETE ends the link."""
    from src.coordination.blackboard import Blackboard
    from src.coordination.board_graph import BoardGraph
    from src.tools import recommendation_tools as rt

    _prepared(link)
    board = Blackboard(claiming_mode="hard")
    bg = BoardGraph(board, preinit=False)
    bg.start(None)
    run_as("peer_3", _set_mission, link)
    run_as("peer_3", _fly, link)
    assert bg.step() is True
    assert not [e for e in bg.log if e.get("event") == "suggestion"], bg.log
    _assert_the_recorded_flight_is_seen(link)
    with agent_scope("mdo_integrator", "mdo_integrator"):
        rt.RecordRecommendation().forward("COMPLETE")
    assert bg.step() is False and bg.stop_reason == "the integrator recorded the verdict COMPLETE"

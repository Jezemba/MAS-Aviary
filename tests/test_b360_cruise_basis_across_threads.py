"""B360: the cruise-point design basis must reach the capture of the INNER get_cruise_point call.

In a real run every tool call goes through type_coercion.bounded_forward -> call_watchdog.bounded, which runs the
whole middleware (resolve_request ... intercept_response) in a worker thread. set_inputs / run_cycle (outer call,
thread T1) ask aviary for the cruise point through the registered get_cruise_point tool (inner call, thread T2).
The basis was a threading.local attribute set in T1 and read in T2 -> always None -> no cruise point was ever valid,
set_inputs went without Fn_DES and run_cycle was refused DESIGN_THRUST_NOT_AVAILABLE (baseline stream, ws8
/data/Jessica/final_fix/baseline2, 2026-11-18). The stub World of test_b340 calls the middleware in ONE thread, so
it never saw this; here every call runs through call_watchdog.bounded exactly like bounded_forward.

Stub servers only, no network, no API, no MCP servers.
"""
import json

import pytest

import src.tools.data_plane as dp
from src.tools import call_watchdog
from tests.test_b340_review_fixes import TRIM_MASS, _expected, world  # noqa: F401 (fixture)


class _ThreadedTool:
    """A registered tool whose middleware runs in a call_watchdog worker thread, as bounded_forward does."""

    def __init__(self, w, name):
        self.world, self.name = w, name

    def forward(self, **kw):
        return json.dumps(call_watchdog.bounded(self.name, self.world.call, self.name, **kw))


def _threaded(w):
    dp.register_tools([_ThreadedTool(w, n) for n in ("set_aircraft_parameters", "get_cruise_point", "set_inputs")])


def _call(w, tool, **kw):
    """An agent's call: the whole middleware in a watchdog thread (type_coercion.bounded_forward)."""
    return call_watchdog.bounded(tool, w.call, tool, **kw)


def test_b360_framework_cruise_point_is_valid_when_calls_run_in_watchdog_threads(world):
    _threaded(world)
    _call(world, "estimate_mass", cpacs_file_path=str(world.cpacs))
    _call(world, "set_inputs", session_id="pc", values={})
    point = dp._design_state.data_store.get("aviary_cruise_point") or {}
    assert point.get("_design_basis") == dp.design_basis()          # was None: the basis stayed in the outer thread
    assert world.fn_des_sent() and world.fn_des_sent()[-1] == pytest.approx(_expected(world))
    out = _call(world, "run_cycle", session_id="pc")
    assert out.get("error_code") != "DESIGN_THRUST_NOT_AVAILABLE" and out.get("success") is not False
    assert dp.engine_is_current()


def test_b360_run_cycle_auto_sizes_in_watchdog_threads(world):
    _threaded(world)
    _call(world, "estimate_mass", cpacs_file_path=str(world.cpacs))
    out = _call(world, "run_cycle", session_id="pc")                 # B187: run_cycle sizes the session itself
    assert out.get("error_code") != "DESIGN_THRUST_NOT_AVAILABLE"
    assert world.fn_des_sent()[-1] == pytest.approx(_expected(world)) and dp.engine_is_current()


def test_b360_the_basis_does_not_leak_after_the_ask(world):
    _threaded(world)
    _call(world, "estimate_mass", cpacs_file_path=str(world.cpacs))
    _call(world, "set_inputs", session_id="pc", values={})
    assert dp._cruise_basis.get() is None                             # reset in the asking context (finally)
    # an agent's own get_cruise_point (no framework ask) carries no basis and is never valid for sizing
    dp._design_state.data_store.pop("aviary_cruise_point", None)
    _call(world, "get_cruise_point", session_id="av", mass_kg=TRIM_MASS, mach=0.78, altitude_ft=33000,
          engine_scale=1.33)
    assert (dp._design_state.data_store.get("aviary_cruise_point") or {}).get("_design_basis") is None

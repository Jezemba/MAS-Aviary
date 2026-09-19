"""B108: SU2 and mass must read the morphed geometry, not the file it was morphed from.

geoauth_all8, sequential iterative, link 2: the wing was morphed to 200.78 m^2 and never exported,
then configure_from_cpacs (REF_AREA 100.39) and estimate_mass ran on the inherited 111.6 m^2 file
while the mission -- under geometry authority -- flew 200.78. A pending morph is now exported
through the registered export_cpacs tool before either call, and refused only if that fails.
"""
import json
import os

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import geometry_guard as gg

MORPH_RESULT = {"component_uid": "Wing1", "new_parameters": {}, "warnings": [],
                "geometry_morph": {"rebuilt": True,
                                   "before": {"span": 35.37, "reference_area": 111.866,
                                              "aspect_ratio": 11.18},
                                   "after": {"span": 45.07, "reference_area": 200.7821502878664,
                                             "aspect_ratio": 10.119, "mac_length": 5.15}}}


class FakeExport:
    """Stands in for the wrapped export_cpacs: writes the file, then the middleware's capture runs."""
    name = "export_cpacs"

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def forward(self, session_id, output_path):
        self.calls.append((session_id, output_path))
        if self.fail:
            return json.dumps({"success": False, "error": "no such session"})
        with open(output_path, "w") as fh:
            fh.write("<cpacs>morphed</cpacs>")
        return dp.intercept_response("export_cpacs", json.dumps({"cpacs_file_path": output_path}))


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.delenv("AVION_DESIGN_AUTHORITY", raising=False)
    monkeypatch.delenv("AVION_REQUIRE_MORPH_EXPORT", raising=False)
    state = DesignState()
    inherited = tmp_path / "geometry_end.xml"
    inherited.write_text("<cpacs>inherited</cpacs>")
    state.cpacs_file_path = str(inherited)
    state.sessions["tigl"] = "tigl-session"
    monkeypatch.setattr(dp, "_design_state", state)
    monkeypatch.setattr(dp, "_registered_tools", {})
    dp._call_ctx.__dict__.clear()
    yield str(inherited)
    dp._call_ctx.__dict__.clear()


def _morph(result=MORPH_RESULT, sid="morph-session"):
    dp.resolve_request("set_high_level_parameters",
                       {"session_id": sid, "component_uid": "Wing1",
                        "updates": {"span": 40.0, "root_chord": 7.0, "tip_chord": 1.9, "sweep": 25.0}})
    dp.intercept_response("set_high_level_parameters", json.dumps(result))


def _pending():
    return dp._design_state.data_store.get("morph_unexported")


def test_a_rebuilt_morph_is_pending_until_exported(tmp_path):
    _morph()
    assert _pending()["session_id"] == "morph-session"
    out = tmp_path / "m.xml"
    out.write_text("x")
    dp.intercept_response("export_cpacs", json.dumps({"cpacs_file_path": str(out)}))
    assert _pending() is None


def test_close_cpacs_auto_export_also_clears_it(tmp_path):
    _morph()
    out = tmp_path / "m.xml"
    out.write_text("x")
    dp.intercept_response("close_cpacs", json.dumps({"cpacs_file_path": str(out)}))
    assert _pending() is None


def test_a_morph_that_did_not_rebuild_is_not_pending():
    _morph({"component_uid": "Wing1", "geometry_morph": {"rebuilt": False}})
    assert _pending() is None


def test_an_export_that_wrote_no_file_leaves_it_pending():
    _morph()
    dp.intercept_response("export_cpacs", json.dumps({"cpacs_file_path": "/nonexistent/x.xml"}))
    assert _pending() is not None


@pytest.mark.parametrize("tool", ["estimate_mass", "configure_from_cpacs"])
def test_the_pending_morph_is_exported_and_used(tool, fresh):
    export = FakeExport()
    dp._registered_tools["export_cpacs"] = export
    _morph()
    resolved = dp.resolve_request(tool, {"cpacs_file_path": fresh})
    assert export.calls and export.calls[0][0] == "morph-session"
    exported = export.calls[0][1]
    assert resolved["cpacs_file_path"] != fresh
    if tool == "configure_from_cpacs":
        assert resolved["cpacs_file_path"] == exported
    else:   # G1: mass writes back into a per-run copy of the export
        assert open(resolved["cpacs_file_path"]).read() == "<cpacs>morphed</cpacs>"
    assert _pending() is None
    assert gg.morph_not_exported(tool, resolved) is None


def test_the_export_happens_once(fresh):
    export = FakeExport()
    dp._registered_tools["export_cpacs"] = export
    _morph()
    dp.resolve_request("configure_from_cpacs", {"cpacs_file_path": fresh})
    dp.resolve_request("estimate_mass", {"cpacs_file_path": fresh})
    assert len(export.calls) == 1


def test_a_new_morph_after_an_export_is_exported_again(fresh):
    export = FakeExport()
    dp._registered_tools["export_cpacs"] = export
    _morph()
    dp.resolve_request("estimate_mass", {"cpacs_file_path": fresh})
    _morph()
    dp.resolve_request("estimate_mass", {"cpacs_file_path": fresh})
    assert len(export.calls) == 2


def test_without_an_export_tool_the_call_is_refused_naming_the_session(fresh):
    _morph()
    resolved = dp.resolve_request("estimate_mass", {"cpacs_file_path": fresh})
    refusal = gg.morph_not_exported("estimate_mass", resolved)
    assert refusal["error_code"] == "MORPH_NOT_EXPORTED"
    assert "morph-session" in refusal["error"] and "200.78" in refusal["error"]


def test_a_failed_export_is_refused(fresh):
    dp._registered_tools["export_cpacs"] = FakeExport(fail=True)
    _morph()
    resolved = dp.resolve_request("configure_from_cpacs", {"cpacs_file_path": fresh})
    assert resolved["cpacs_file_path"] == fresh
    assert gg.morph_not_exported("configure_from_cpacs", resolved)["error_code"] == "MORPH_NOT_EXPORTED"


def test_nothing_pending_means_nothing_happens(fresh):
    export = FakeExport()
    dp._registered_tools["export_cpacs"] = export
    resolved = dp.resolve_request("estimate_mass", {"cpacs_file_path": fresh})
    assert not export.calls
    assert gg.morph_not_exported("estimate_mass", resolved) is None


def test_other_tools_are_not_touched(fresh):
    export = FakeExport()
    dp._registered_tools["export_cpacs"] = export
    _morph()
    assert gg.morph_not_exported("run_simulation", {}) is None
    dp.resolve_request("validate_cpacs_inputs", {"cpacs_file_path": fresh})
    assert not export.calls


def test_mission_authority_stands_down(monkeypatch, fresh):
    monkeypatch.setenv("AVION_DESIGN_AUTHORITY", "mission")
    export = FakeExport()
    dp._registered_tools["export_cpacs"] = export
    _morph()
    resolved = dp.resolve_request("estimate_mass", {"cpacs_file_path": fresh})
    assert not export.calls
    assert gg.morph_not_exported("estimate_mass", resolved) is None


def test_the_switch_turns_the_refusal_off(monkeypatch, fresh):
    monkeypatch.setenv("AVION_REQUIRE_MORPH_EXPORT", "0")
    _morph()
    assert gg.morph_not_exported("estimate_mass", {"cpacs_file_path": fresh}) is None

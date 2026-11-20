"""X4 (validation 3, 2026-11-19): no CPACS export may land in a folder that several runs share.

runs/<tag>/mass-mcp is a symlink to the machine's one code/mass-mcp checkout. O_SP and S_IF exported their wing to
<run>/mass-mcp/geometry/updated_D150_simple.xml, i.e. into that shared checkout; with three combinations at once on one
machine, one combination's mass estimate or carried geometry could read another's wing. Two simulated runs whose
mass-mcp symlinks point at the same folder must not see each other's export. Stub tools only: no server, no model.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.coordination.design_state import DesignState
from src.tools import data_plane as dp

NAME = "updated_D150_simple.xml"


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """One shared mass-mcp checkout, two run folders linking to it (the B251 layout)."""
    shared = tmp_path / "code" / "mass-mcp"
    (shared / ".git").mkdir(parents=True)
    (shared / "tests" / "fixtures").mkdir(parents=True)
    (shared / "tests" / "fixtures" / "D150_simple.xml").write_text("<cpacs>BASELINE</cpacs>")
    runs = {}
    for tag in ("combo_a", "combo_b"):
        run = tmp_path / "runs" / tag
        run.mkdir(parents=True)
        os.symlink(shared, run / "mass-mcp")
        runs[tag] = run
    monkeypatch.setattr(dp, "_registered_tools", {})
    monkeypatch.setattr(dp, "_presessions", {})
    monkeypatch.setattr(dp, "_tool_server_map", {"estimate_mass": "mass"})
    monkeypatch.setattr(dp, "_FRAMEWORK_PRIVATE_CPACS_DIRS", set())
    monkeypatch.setenv("AVION_ENGINE_DECK", "0")      # no carried engine in a unit test (else ENGINE_NOT_ON_RECORD)
    dp.take_refusal()
    return shared, runs


def _new_run():
    dp.init_data_plane(DesignState(), {"estimate_mass": "mass"})
    return dp._design_state


def _export(typed: str, content: str) -> str:
    """What the middleware + tigl do for export_cpacs: resolve, write at the resolved path, capture the response."""
    resolved = dp.resolve_request("export_cpacs", {"session_id": "s1", "output_path": typed})
    assert dp.take_refusal() is None
    out = resolved["output_path"]
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(content)
    dp.intercept_response("export_cpacs", json.dumps({"status": "success", "cpacs_file_path": out}))
    return out


def test_two_runs_on_one_shared_mass_mcp_never_see_each_others_export(machine, tmp_path):
    shared, runs = machine
    typed_a = str(runs["combo_a"] / "mass-mcp" / "geometry" / NAME)
    typed_b = str(runs["combo_b"] / "mass-mcp" / "geometry" / NAME)
    assert os.path.realpath(os.path.dirname(typed_a)) == os.path.realpath(os.path.dirname(typed_b))   # same folder

    state_a = _new_run()
    file_a = _export(typed_a, "<cpacs>WING_A</cpacs>")
    state_b = _new_run()
    file_b = _export(typed_b, "<cpacs>WING_B</cpacs>")

    # Nothing was written into the shared checkout; each export is in its own run's private folder.
    assert not (shared / "geometry").exists()
    assert file_a != file_b and os.path.dirname(file_a) != os.path.dirname(file_b)
    for f in (file_a, file_b):
        assert os.path.basename(f) == NAME and not os.path.realpath(f).startswith(str(shared.resolve()))

    # Run A (after run B wrote) still reads its own wing, by every route the downstream uses.
    dp.init_data_plane(state_a, {"estimate_mass": "mass"})
    assert state_a.data_store["morphed_cpacs_path"] == file_a
    mass_in = dp.resolve_request("estimate_mass", {"cpacs_file_path": typed_a})["cpacs_file_path"]
    assert Path(mass_in).read_text() == "<cpacs>WING_A</cpacs>"
    opened = dp.resolve_request("open_cpacs", {"source_type": "path", "source": typed_a})
    assert dp.take_refusal() is None and opened["source"] == file_a          # re-opening the typed path: its own file
    su2 = dp.resolve_request("create_su2_session", {"cpacs_file_path": typed_a})
    assert Path(su2["cpacs_file_path"]).read_text() == "<cpacs>WING_A</cpacs>"
    from scripts.stat_batch_runner import carry_geometry_forward

    carried = carry_geometry_forward(state_a.data_store, tmp_path / "link_a")
    assert Path(carried).read_text() == "<cpacs>WING_A</cpacs>"

    # ... and run B its own.
    dp.init_data_plane(state_b, {"estimate_mass": "mass"})
    mass_in = dp.resolve_request("estimate_mass", {"cpacs_file_path": typed_b})["cpacs_file_path"]
    assert Path(mass_in).read_text() == "<cpacs>WING_B</cpacs>"
    assert Path(carry_geometry_forward(state_b.data_store, tmp_path / "link_b")).read_text() == "<cpacs>WING_B</cpacs>"


def test_the_same_typed_path_keeps_one_file_per_link_and_spellings_share_it(machine):
    shared, runs = machine
    _new_run()
    via_link = str(runs["combo_a"] / "mass-mcp" / "geometry" / NAME)
    via_checkout = str(shared / "geometry" / NAME)
    first = _export(via_link, "x")
    assert _export(via_link, "y") == first
    assert _export(via_checkout, "z") == first             # the symlink and the checkout are the same target
    # the next link (a new design state) gets a new folder
    _new_run()
    assert _export(via_link, "w") != first


@pytest.mark.parametrize("typed", ["/tmp/morphed_design.xml", "morphed_design.xml", "geometry/out.xml"])
def test_tmp_and_relative_paths_are_redirected_too(machine, typed):
    _new_run()
    out = dp.resolve_request("export_cpacs", {"session_id": "s1", "output_path": typed})["output_path"]
    assert out != typed and os.path.isabs(out) and dp._is_private_cpacs_path(out)
    assert os.path.basename(out) == os.path.basename(typed)


def test_framework_private_folders_are_not_redirected(machine, tmp_path):
    _new_run()
    own = os.path.join(dp._new_private_dir("avion_morph_"), "morphed_design.xml")      # the B108 auto-export
    assert dp.resolve_request("export_cpacs", {"session_id": "s1", "output_path": own})["output_path"] == own
    evaluator_dir = tmp_path / "opt" / "cpacs"
    evaluator_dir.mkdir(parents=True)
    dp.register_private_cpacs_dir(evaluator_dir)                                       # optimizer evaluator
    target = str(evaluator_dir / "eval_00000.xml")
    assert dp.resolve_request("export_cpacs", {"session_id": "s1", "output_path": target})["output_path"] == target


def test_a_file_another_run_left_in_the_shared_checkout_is_refused(machine):
    shared, runs = machine
    (shared / "geometry").mkdir()
    (shared / "geometry" / NAME).write_text("<cpacs>SOMEONE_ELSES_WING</cpacs>")
    typed = str(runs["combo_a"] / "mass-mcp" / "geometry" / NAME)
    _new_run()
    dp.resolve_request("open_cpacs", {"source_type": "path", "source": typed})
    refusal = dp.take_refusal()
    assert refusal and refusal["error_code"] == "CPACS_SOURCE_SHARED" and refusal["resolved_path"].endswith(NAME)
    assert dp._design_state.cpacs_file_path != typed
    dp.resolve_request("estimate_mass", {"cpacs_file_path": typed})
    assert (dp.take_refusal() or {}).get("error_code") == "CPACS_SOURCE_SHARED"
    assert len(dp._design_state.data_store["_shared_cpacs_refusals"]) == 2


def test_the_tracked_fixture_and_files_outside_any_checkout_still_open(machine, tmp_path):
    shared, runs = machine
    _new_run()
    fixture = str(runs["combo_a"] / "mass-mcp" / "tests" / "fixtures" / "D150_simple.xml")
    dp.resolve_request("open_cpacs", {"source_type": "path", "source": fixture})
    assert dp.take_refusal() is None and dp._design_state.cpacs_file_path == fixture
    elsewhere = tmp_path / "chain" / "geometry_end.xml"
    elsewhere.parent.mkdir()
    elsewhere.write_text("<cpacs/>")
    dp.resolve_request("open_cpacs", {"source_type": "path", "source": str(elsewhere)})
    assert dp.take_refusal() is None


def test_a_mass_write_back_without_a_private_copy_is_refused(machine, monkeypatch):
    shared, runs = machine
    _new_run()
    fixture = str(runs["combo_a"] / "mass-mcp" / "tests" / "fixtures" / "D150_simple.xml")
    monkeypatch.setattr(dp, "_mass_working_copy", lambda *a, **k: None)
    dp.resolve_request("estimate_mass", {"cpacs_file_path": fixture})
    assert (dp.take_refusal() or {}).get("error_code") == "CPACS_WRITEBACK_UNSAFE"
    dp.resolve_request("estimate_mass", {"cpacs_file_path": fixture, "write_back_to_cpacs": False})
    assert dp.take_refusal() is None

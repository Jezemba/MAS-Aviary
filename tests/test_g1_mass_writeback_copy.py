"""G1: estimate_mass must never write its breakdown into the CPACS file it was given.

mass-mcp's write_back_to_cpacs defaults to True, so a run writing into the tracked
D150 fixture leaked its masses into every later run that opened the fixture as
its start. Observed again on the 7960 on 2026-09-14 within one run.
"""

import hashlib
import json
import os
import socket
import subprocess
import time
from pathlib import Path

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState

MAS = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def fresh_plane(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_registered_tools", {})
    monkeypatch.setattr(dp, "_presessions", {})
    monkeypatch.setattr(dp, "_tool_server_map", {
        "estimate_mass": "mass", "get_cpacs_mass_breakdown": "mass", "validate_cpacs_inputs": "mass",
    })
    yield


@pytest.fixture
def cpacs(tmp_path):
    p = tmp_path / "fixture.xml"
    p.write_text("<cpacs><vehicles/></cpacs>")
    return p


def test_write_back_is_redirected_to_an_identical_copy(cpacs):
    out = dp.resolve_request("estimate_mass", {"cpacs_file_path": str(cpacs)})
    copy = out["cpacs_file_path"]
    assert copy != str(cpacs) and os.path.isfile(copy)
    assert Path(copy).read_bytes() == cpacs.read_bytes()
    assert dp._design_state.data_store["mass_writeback_cpacs_path"] == copy


def test_repeated_calls_reuse_the_same_copy(cpacs):
    a = dp.resolve_request("estimate_mass", {"cpacs_file_path": str(cpacs)})["cpacs_file_path"]
    b = dp.resolve_request("estimate_mass", {"cpacs_file_path": str(cpacs)})["cpacs_file_path"]
    assert a == b


def test_reads_follow_the_copy_so_they_see_what_the_run_wrote(cpacs):
    assert dp.resolve_request("get_cpacs_mass_breakdown", {"cpacs_file_path": str(cpacs)})["cpacs_file_path"] == str(cpacs)
    copy = dp.resolve_request("estimate_mass", {"cpacs_file_path": str(cpacs)})["cpacs_file_path"]
    assert dp.resolve_request("get_cpacs_mass_breakdown", {"cpacs_file_path": str(cpacs)})["cpacs_file_path"] == copy
    assert dp.resolve_request("validate_cpacs_inputs", {"cpacs_file_path": str(cpacs)})["cpacs_file_path"] == copy


def test_no_write_back_means_no_copy(cpacs):
    out = dp.resolve_request("estimate_mass", {"cpacs_file_path": str(cpacs), "write_back_to_cpacs": False})
    assert out["cpacs_file_path"] == str(cpacs)


def test_copy_is_refreshed_when_the_source_geometry_changes(cpacs):
    first = dp.resolve_request("estimate_mass", {"cpacs_file_path": str(cpacs)})["cpacs_file_path"]
    time.sleep(0.01)
    cpacs.write_text("<cpacs><vehicles><changed/></vehicles></cpacs>")
    second = dp.resolve_request("estimate_mass", {"cpacs_file_path": str(cpacs)})["cpacs_file_path"]
    assert second != first
    assert Path(second).read_bytes() == cpacs.read_bytes()


def test_passing_the_copy_itself_does_not_copy_again(cpacs):
    copy = dp.resolve_request("estimate_mass", {"cpacs_file_path": str(cpacs)})["cpacs_file_path"]
    assert dp.resolve_request("estimate_mass", {"cpacs_file_path": copy})["cpacs_file_path"] == copy


def test_morphed_export_is_also_copied_not_written(cpacs, tmp_path):
    morphed = tmp_path / "morphed.xml"
    morphed.write_text("<cpacs><morphed/></cpacs>")
    dp._design_state.data_store["morphed_cpacs_path"] = str(morphed)
    out = dp.resolve_request("estimate_mass", {"cpacs_file_path": str(cpacs)})["cpacs_file_path"]
    assert out not in (str(cpacs), str(morphed))
    assert Path(out).read_bytes() == morphed.read_bytes()


def _up(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _up(8700), reason="live mass server on 127.0.0.1:8700 required")
def test_live_estimate_mass_leaves_the_tracked_fixture_untouched(monkeypatch):
    from scripts.stat_batch_runner import _D150_FIXTURE
    from src.config.loader import load_config
    from src.tools.tool_loader import load_tools_for_agent

    monkeypatch.setattr(dp, "_design_state", None)
    fixture = Path(_D150_FIXTURE)
    before = hashlib.sha256(fixture.read_bytes()).hexdigest()
    tools = {t.name: t for t in load_tools_for_agent([], load_config("config/mdo_f25_run_qwen32b.yaml"))}

    r = tools["estimate_mass"].forward(cpacs_file_path=str(fixture), wing_mass_method="flops")
    d = json.loads(r) if isinstance(r, str) else r
    assert d.get("status") == "success", d

    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == before, "fixture was modified"
    dirty = subprocess.run(["git", "-C", str(fixture.parents[2]), "status", "--porcelain", "--",
                            str(fixture.relative_to(fixture.parents[2]))], capture_output=True, text=True).stdout
    assert dirty == "", dirty

    copy = dp.get_design_state().data_store["mass_writeback_cpacs_path"]
    assert "massBreakdown" in Path(copy).read_text() and "mass-mcp" in Path(copy).read_text()
    # The agent still passes the fixture path; the read is served from the copy,
    # so it sees the breakdown this run wrote.
    b = tools["get_cpacs_mass_breakdown"].forward(cpacs_file_path=str(fixture))
    bd = json.loads(b) if isinstance(b, str) else b
    assert bd["status"] == "success", bd
    assert bd["cpacs_file_path"] == copy
    assert bd["mass_breakdown"]["mOEM_kg"] > 0 and bd["mass_breakdown"]["components"]["mWing_source"] == "flops"

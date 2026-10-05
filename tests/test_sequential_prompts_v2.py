"""sequential-prompts-v2 (Jessica, 2026-10-04): her prompts verbatim, KB handoff from the records,
best practices on request, framework-written design.json."""

import json
import re
from pathlib import Path

import pytest
import yaml

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import knowledge_base as kbm

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "sequential.md"


def _her_prompts():
    text = SRC.read_text()
    parts = re.split(r"^(#{2,3} .+)$", text, flags=re.M)
    sec = {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}

    def ex(body):
        m = re.search(r"^```[^\n]*\n(.*?)^```", body, flags=re.M | re.S)
        return (m.group(1) if m else body).strip("\n")
    return {
        "BASE": ex(sec["### Initial Prompt"]),
        "geometry_engineer": ex(sec["## Agent: Geometry Engineer"]),
        "aerodynamics_analyst": ex(sec["## Agent: Aerodynamics Analyst"]),
        "structures_analyst": ex(sec["## Agent: structures_analyst"]),
        "propulsion_analyst": ex(sec["## Agent: Propulsion Agent"]),
        "mission_architect": ex(sec["## Agent: Mission Architect"]),
        "simulation_executor": ex(sec["## Agent: Simulation Executor"]),
        "mdo_integrator": ex(sec["## Agent: Mdo Integrator"]),
    }


def _norm(t):
    return "\n".join(line.rstrip() for line in t.strip("\n").split("\n"))


@pytest.mark.skipif(not SRC.exists(), reason="sequential.md not in this checkout")
def test_the_yaml_carries_her_prompts_verbatim():
    p = _her_prompts()
    d = yaml.safe_load((ROOT / "config/mdo_f25_sequential_agents.yaml").read_text())
    assert _norm(d["stage_defaults"]["base_instructions"]) == _norm(p["BASE"])
    for st in d["templates"]["mdo_f25"]["stages"]:
        assert _norm(st["role"]) == _norm(p[st["name"]]), st["name"]
        assert st.get("interface_output") == ""            # her DELIVERABLE line is not duplicated


def test_the_staged_pipeline_adds_no_stage_prompt():
    d = yaml.safe_load((ROOT / "config/mdo_f25_staged_pipeline_sequential.yaml").read_text())
    assert all(s["stage_prompt"] == "" for s in d["mdo_f25_pipeline"]["stages"])


# ---- KB handoff from the records ------------------------------------------------------------------

@pytest.fixture
def kb(monkeypatch, tmp_path):
    monkeypatch.setenv("MAS_KB_HANDOFF", "recent")
    monkeypatch.setattr(dp, "_design_state", DesignState())
    kbm.configure_run(path=str(tmp_path / "knowledge_base.jsonl"), chain_link=0, attempt=1, seed=None)
    k = kbm.get_kb()
    yield k
    kbm.configure_run(None, None, None, None)


def test_the_handoff_is_the_latest_records_newest_last(kb):
    kb.append(tool="generate_volume_mesh", server="tigl", agent="geometry_engineer", role="",
              status="success", outputs={"element_count": 2257805})
    kb.append(tool="run_su2_solver", server="su2", agent="aerodynamics_analyst", role="",
              status="success", outputs={"final_coefficients": {"CL": 0.205, "CD": 0.0021}})
    text = kbm.handoff_text("TASK")
    assert "no record of SU2" not in text and "run_su2_solver" in text
    assert text.index("generate_volume_mesh") < text.index("run_su2_solver")      # newest last
    assert re.search(r"\[\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\]", text)                  # timestamped
    assert text.rstrip().endswith("TASK")


def test_recent_records_drops_the_oldest_when_too_long():
    rows = [{"seq": i, "time_utc": "2026-10-04T00:00:00Z", "agent": "a", "tool": f"t{i}",
             "status": "success", "outputs": {"x": "y" * 200}} for i in range(40)]
    text = kbm.recent_records(rows, limit_lines=30, limit_chars=2000)
    assert "t39" in text and "t0 " not in text and len(text) <= 2000


def test_the_summary_mode_still_uses_the_summary_model_by_default(monkeypatch):
    monkeypatch.delenv("MAS_KB_HANDOFF", raising=False)
    assert kbm.handoff_mode() == "summary"


# ---- tools ------------------------------------------------------------------------------------------

def test_best_practices_sections():
    from src.tools.best_practices_tool import ReadBestPractices

    out = ReadBestPractices().forward("propulsion")
    assert "## general" in out and "## propulsion" in out and "## geometry" not in out
    assert "perf.TSFC" in out


def test_best_practices_do_not_repeat_fixed_bugs():
    text = (ROOT / "config/best_practices.md").read_text()
    assert 'component_uid="<<WING_UID>>"' not in text and "max_runtime_seconds=300" not in text
    assert "does NOT modify" not in text


def test_design_json_is_written_next_to_the_knowledge_base(monkeypatch, tmp_path):
    from src.tools.design_json import ReadDesignJson

    kbm.configure_run(path=str(tmp_path / "knowledge_base.jsonl"), chain_link=0, attempt=1, seed=None)
    try:
        monkeypatch.setattr(dp, "_design_state", DesignState())
        dp._design_state.data_store.update({
            "geometry_wing": {"Aircraft.Wing.AREA": 130.0, "_span": 34.9},
            "aero_cl_cruise": 0.205, "aero_cd_cruise": 0.0021, "mass_mtom_kg": 78126.0})
        dp.intercept_response("get_su2_status", json.dumps({"installed": True}))
        doc = json.loads((tmp_path / "design.json").read_text())
        assert doc["geometry"]["wing"] == {"Aircraft.Wing.AREA": 130.0}
        assert doc["aero"]["cd_cruise"] == 0.0021 and doc["structures"]["mtom_kg"] == 78126.0
        assert doc["mission"]["range_nmi"] == 2500
        assert json.loads(ReadDesignJson().forward())["aero"]["cl_cruise"] == 0.205
    finally:
        kbm.configure_run(None, None, None, None)


def test_every_agent_gets_the_two_new_tools():
    from src.tools.tool_loader import _with_design_state

    names = {t.name for t in _with_design_state([])}
    assert {"read_best_practices", "read_design_json", "get_design_state"} <= names


# ---- the study's fixed controls are held by the framework -------------------------------------------

@pytest.fixture
def ds(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    return dp._design_state


def test_configure_mission_is_pinned_to_the_study_mission(ds):
    out = dp.resolve_request("configure_mission", {"session_id": "a", "range_nmi": 1500, "num_passengers": 160,
                                                   "cruise_mach": 0.785, "cruise_altitude_ft": 35000})
    assert (out["range_nmi"], out["num_passengers"], out["cruise_mach"], out["cruise_altitude_ft"]) == \
        (2500, 239, 0.78, 33000)
    resp = json.loads(dp.intercept_response("configure_mission", json.dumps({"success": True})))
    assert resp["study_controls"]["overridden"]["range_nmi"] == {"asked": 1500, "used": 2500}


def test_a_matching_mission_is_not_reported_as_overridden(ds):
    dp.resolve_request("configure_mission", {"session_id": "a", "range_nmi": 2500.0, "cruise_mach": 0.78})
    resp = json.loads(dp.intercept_response("configure_mission", json.dumps({"success": True})))
    assert "study_controls" not in resp


def test_estimate_mass_method_and_material_are_pinned(ds):
    out = dp.resolve_request("estimate_mass", {"cpacs_file_path": "/nonexistent.xml",
                                               "wing_mass_method": "oas", "material": "composite"})
    assert out["wing_mass_method"] == "flops" and out["material"] == "aluminum"
    assert out["design_load_factor"] == 2.5


# ---- B142: one runner per output folder -------------------------------------------------------------

def test_a_second_runner_cannot_take_the_output_folder(tmp_path):
    import subprocess
    import sys

    from scripts.stat_batch_runner import acquire_output_lock

    assert acquire_output_lock(tmp_path) is True
    code = ("import sys; sys.path.insert(0, %r); from pathlib import Path; "
            "from scripts.stat_batch_runner import acquire_output_lock; "
            "sys.exit(0 if acquire_output_lock(Path(%r)) else 3)") % (str(ROOT), str(tmp_path))
    other = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT))
    assert other.returncode == 3                                   # refused while this one holds it


# ---- the run's files reach W&B ----------------------------------------------------------------------

def test_run_files_and_the_log_are_uploaded(monkeypatch, tmp_path):
    import scripts.stat_batch_runner as sbr

    link = tmp_path / "repeat_000" / "mdo_f25_sequential_staged_pipeline"
    link.mkdir(parents=True)
    for name in ("knowledge_base.jsonl", "design.json", "result.json", "trace.json"):
        (link / name).write_text("{}\n")
    (link / "step_log_attempt1.jsonl").write_text(json.dumps(
        {"seq": 1, "kind": "step", "agent": "geometry_engineer", "agent_step": 1, "generations": [],
         "tool_calls": [], "observations": ""}) + "\n")
    (tmp_path / "chain_anchor.json").write_text("{}")
    log = tmp_path / "run.log"
    log.write_text("console\n")
    monkeypatch.setenv("SWEEP_LOG_PATH", str(log))
    saved = []
    monkeypatch.setattr(sbr.wandb, "save", lambda path, base_path=None, policy=None: saved.append(path))
    sbr.upload_run_files(object(), tmp_path)
    names = {Path(p).name for p in saved}
    assert {"knowledge_base.jsonl", "design.json", "result.json", "trace.json", "chain_anchor.json",
            "step_log_attempt1.jsonl", "step_log_attempt1.md", "run.log"} <= names

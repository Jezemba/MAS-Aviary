"""D2 / D3: every analysis input that belongs to one study (its baseline, links.csv, agent_bounds.json, the plots
log) is given explicitly and refused when it is another study's. No MCP, model or run data."""

import json
import sys

import pandas as pd
import pytest

from analysis.link20 import agent_bounds as ab
from analysis.link20 import extract as ex


def _baseline_file(tmp_path, fuel):
    p = tmp_path / f"baseline_{fuel}.json"
    p.write_text(json.dumps({"run_simulation": {"summary": {"fuel_burned_kg": fuel}}}))
    return p


def _run_main(module_main, argv):
    old = sys.argv
    sys.argv = argv
    try:
        return module_main()
    finally:
        sys.argv = old


def test_there_is_no_default_study(monkeypatch):
    monkeypatch.delenv("LINK20_STUDY", raising=False)
    for name in ("STUDY", "SOURCES", "BASELINE"):
        with pytest.raises(SystemExit, match="No study selected"):
            getattr(ex, name)
    with pytest.raises(SystemExit, match="Unknown study"):
        ex.require_study("20990101")


def test_env_study_selects_runs_and_baseline(monkeypatch):
    monkeypatch.setenv("LINK20_STUDY", "final")
    assert ex.SOURCES is ex.STUDIES["final"] and ex.BASELINE == ex.BASELINES["final"]
    assert ex.BASELINE["fuel_kg"] == 16390.5
    monkeypatch.setenv("LINK20_STUDY", "20261028")
    assert ex.BASELINE["fuel_kg"] == 18197.0


def test_baseline_file_must_be_the_studys(tmp_path):
    assert ex.checked_baseline_fuel(_baseline_file(tmp_path, 16390.52), "final") == 16390.52
    with pytest.raises(SystemExit, match="wrong baseline file"):
        ex.checked_baseline_fuel(_baseline_file(tmp_path, 18197.01), "final")


def test_links_csv_must_be_the_studys():
    ex.check_links_study(pd.DataFrame({"study": ["final", "final"]}), "final")
    with pytest.raises(SystemExit, match="not 'final'"):
        ex.check_links_study(pd.DataFrame({"study": ["20261028"]}), "final")
    with pytest.raises(SystemExit, match="no `study` column"):
        ex.check_links_study(pd.DataFrame({"link": [1]}), "final")


def test_extract_requires_study_and_writes_it(tmp_path):
    with pytest.raises(SystemExit):
        _run_main(ex.main, ["extract", "--out", str(tmp_path)])
    root = tmp_path / "runs"
    d = root / "repeat_000" / "mdo_f25_sequential_staged_pipeline"
    d.mkdir(parents=True)
    (d / "knowledge_base.jsonl").write_text("")
    df = ex.build_table([("t", root, ["mdo_f25_sequential_staged_pipeline"])], "final")
    assert df["study"].tolist() == ["final"]


def test_optimizer_compare_refuses_another_studys_baseline(tmp_path):
    from analysis.link20 import optimizer_compare as oc

    links = tmp_path / "links.csv"
    pd.DataFrame({"study": ["final"], "link": [1]}).to_csv(links, index=False)
    old = _baseline_file(tmp_path, 18197.0)
    with pytest.raises(SystemExit, match="wrong baseline file"):
        _run_main(oc.main, ["oc", "--study", "final", "--links", str(links), "--baseline", str(old),
                            "--runs", str(tmp_path / "pso"), "--out", str(tmp_path / "o")])
    with pytest.raises(SystemExit):           # --baseline is required (it was optional and silently dropped)
        _run_main(oc.main, ["oc", "--study", "final", "--links", str(links), "--runs", str(tmp_path / "pso"),
                            "--out", str(tmp_path / "o")])


def test_eval_counts_takes_the_study_explicitly():
    import inspect

    from analysis.link20 import eval_counts as ec
    assert inspect.signature(ec.mas_table).parameters["sources"].default is inspect.Parameter.empty


# ---------------------------------------------------------------- D3: agent_bounds.json per study

def _bounds(tmp_path, study, roots=None):
    p = tmp_path / f"agent_bounds_{study}.json"
    roots = roots if roots is not None else sorted({str(r) for _d, r, _c in ex.STUDIES[study]})
    p.write_text(json.dumps({"study": study, "run_roots": roots, "bounds": {"wing_area": {"min": 1, "max": 2}}}))
    return p


def test_bounds_file_of_another_study_is_refused(tmp_path):
    assert ab.load_checked(_bounds(tmp_path, "final"), "final")["bounds"]["wing_area"]["max"] == 2
    with pytest.raises(SystemExit, match="is from study '20261028', not 'final'"):
        ab.load_checked(_bounds(tmp_path, "20261028"), "final")
    legacy = tmp_path / "legacy.json"                     # the old file had no study at all
    legacy.write_text(json.dumps({"links": 155, "bounds": {}}))
    with pytest.raises(SystemExit, match="is from study None"):
        ab.load_checked(legacy, "final")
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(SystemExit, match="read run folders"):
        ab.load_checked(_bounds(other, "final", roots=["/elsewhere"]), "final")


def test_design_params_refuses_a_mismatched_bounds_file(tmp_path):
    from analysis.link20.reports.scripts import design_params as dp

    links = tmp_path / "links.csv"
    pd.DataFrame({"study": ["final"], "link": [1]}).to_csv(links, index=False)
    log = tmp_path / "plots_run.log"
    log.write_text("baseline refs (final): {'Aircraft.Wing.AREA': 122.8}\n")
    argv = ["dp", "--links", str(links), "--recs", str(tmp_path / "r.csv"), "--plots-log", str(log),
            "--out", str(tmp_path / "out"), "--study", "final"]
    with pytest.raises(SystemExit, match="not 'final'"):
        _run_main(dp.main, argv + ["--bounds", str(_bounds(tmp_path, "20261028"))])
    assert dp.baseline_refs(log, "final") == {"Aircraft.Wing.AREA": 122.8}
    with pytest.raises(SystemExit, match="does not start with"):
        dp.baseline_refs(log, "20261028")


# ---------------------------------------------------------------- V13: the final code's studies

def test_final_is_still_l20_final_and_old_studies_unchanged():
    assert ex.STUDIES["final"] == ex.SOURCES_FINAL
    assert all(str(root).startswith("/data/Jessica/l20_final/runs/") for _d, root, _c in ex.STUDIES["final"])
    assert ex.BASELINES["final"]["fuel_kg"] == 16390.5 and ex.BASELINES["20261112"]["fuel_kg"] == 16390.5
    assert ex.BASELINES["20261028"]["fuel_kg"] == 18197.0


def test_validation_study_has_the_final_codes_baseline():
    combos = [c for _d, _r, cs in ex.STUDIES["final_val"] for c in cs]
    assert len(combos) == 8 and len(set(combos)) == 8
    assert ex.BASELINES["final_val"] == {"fuel_kg": 15719.18, "gtow_kg": 90310.01, "wing_mass_kg": 7958.8}
    assert ex.BASELINES["final_rerun"] == ex.BASELINES["final_val"]


def test_driver_flight_record_is_a_baseline_file(tmp_path):
    p = tmp_path / "B2_coupled_anchor_1.json"
    p.write_text(json.dumps({"fuel_burned_kg": 15719.17609579942, "gtow_kg": 90310.0055, "converged": True}))
    assert ex.checked_baseline_fuel(p, "final_val") == pytest.approx(15719.176, abs=1e-3)
    with pytest.raises(SystemExit, match="wrong baseline file"):
        ex.checked_baseline_fuel(p, "final")
    p.write_text(json.dumps({"fuel_burned_kg": 15719.18, "converged": False}))
    with pytest.raises(SystemExit, match="did not converge"):
        ex.checked_baseline_fuel(p, "final_val")


def test_final_rerun_needs_its_root_and_discovers_the_runs(tmp_path, monkeypatch):
    monkeypatch.delenv(ex.FINAL_RERUN_ROOT_ENV, raising=False)
    with pytest.raises(SystemExit, match="LINK20_FINAL_RERUN_ROOT"):
        ex.STUDIES["final_rerun"]
    assert "final_rerun" in ex.STUDIES                       # still a known study (argparse choices)
    for tag, combo in (("fin_seq_staged", "mdo_f25_sequential_staged_pipeline"),
                       ("fin_net_graph", "mdo_f25_networked_graph_routed")):
        (tmp_path / "runs" / tag / "MAS-Aviary" / "logs" / "stat_results" / tag / "repeat_000" / combo).mkdir(
            parents=True)
    monkeypatch.setenv(ex.FINAL_RERUN_ROOT_ENV, str(tmp_path))
    found = {d: (str(r), c) for d, r, c in ex.STUDIES["final_rerun"]}
    assert found["fin_net_graph"][1] == ["mdo_f25_networked_graph_routed"]
    assert found["fin_seq_staged"][0].endswith("stat_results/fin_seq_staged")
    monkeypatch.setenv(ex.FINAL_RERUN_ROOT_ENV, str(tmp_path / "empty"))
    with pytest.raises(SystemExit, match="no run folder"):
        ex.STUDIES["final_rerun"]


def test_plain_stat_results_layout_is_discovered(tmp_path):
    (tmp_path / "runs" / "l20_seq_iter" / "repeat_003" / "mdo_f25_sequential_iterative_feedback").mkdir(parents=True)
    assert [(d, c) for d, _r, c in ex.discover_sources(tmp_path)] == [
        ("l20_seq_iter", ["mdo_f25_sequential_iterative_feedback"])]

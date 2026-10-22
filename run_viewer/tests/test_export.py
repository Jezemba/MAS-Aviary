"""Tests for the static export: no machine paths, live runs become snapshots, layout is complete."""

import json

from run_viewer import export_static


def test_sanitize_strips_paths_and_server_fields():
    obj = {"run_dir": "/home/x/run", "a": "/home/aipexws8/Jessica/tmp/su2_session_1/config.cfg",
           "b": ["/tmp/avion_export_1/D150.xml", "plain text with /home/ inside"], "workdir": "/tmp/w",
           "n": 1.5, "c": {"geometry": "/data/avion_runs/v3/geometry_end.xml"}}
    out = export_static.sanitize(obj)
    assert "run_dir" not in out and "workdir" not in out
    assert out["a"] == "config.cfg" and out["b"][0] == "D150.xml"
    assert out["b"][1] == "plain text with /home/ inside"  # a bare prefix is not a path
    assert out["c"]["geometry"] == "geometry_end.xml" and out["n"] == 1.5
    text = '{"cpacs_file_path": "/home/aipexws5/Jessica/Avion/x/geometry_end.xml", "n": 2}'
    assert export_static.sanitize(text) == '{"cpacs_file_path": "…/geometry_end.xml", "n": 2}'


def test_export_run_makes_a_snapshot(tmp_path):
    bundles = tmp_path / "bundles" / "r1"
    (bundles / "links").mkdir(parents=True)
    (bundles / "img").mkdir()
    (bundles / "run.json").write_text(json.dumps({"run_id": "r1", "title": "T", "live": True,
                                                  "run_dir": "/home/a/b", "links": [{"link": 1}]}))
    (bundles / "links" / "001.json").write_text(json.dumps({"link": 1, "images": {"x": "/tmp/q/a.png"}}))
    (bundles / "img" / "001_su2.png").write_bytes(b"png")
    (bundles / "img" / "001_su2.png.json").write_text("{}")
    out = tmp_path / "site"
    row = export_static.export_run({"run_id": "r1", "combination": "c", "live": True, "note": "n"},
                                   str(tmp_path / "bundles"), str(out), "2026-10-22 12:00 UTC")
    assert row["live"] is False and "Snapshot of a run in progress, taken 2026-10-22 12:00 UTC" in row["note"]
    run = json.loads((out / "data" / "r1" / "run.json").read_text())
    assert run["live"] is False and "run_dir" not in run
    assert json.loads((out / "data" / "r1" / "links" / "001.json").read_text())["images"]["x"] == "a.png"
    assert (out / "data" / "r1" / "img" / "001_su2.png").exists()
    assert not (out / "data" / "r1" / "img" / "001_su2.png.json").exists()  # build cache not exported

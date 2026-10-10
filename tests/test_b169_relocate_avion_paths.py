"""B169: the sequential prompts name the D150 by its aipexws5 path; every machine must load its own copy.

Reported by the aipexws8 setup agent (2026-10-10): /home/aipexws5 does not exist there, so the agents would
have been handed a CPACS path that does not exist. Jessica's text is not edited; the loader relocates it.
"""

from pathlib import Path

import yaml

from src.config.canonical import AVION_ROOT, relocate_avion_paths, substitute_obj
from src.config.loader import load_yaml

MAS = Path(__file__).resolve().parents[1]
AGENTS = MAS / "config" / "mdo_f25_sequential_agents.yaml"
WRITTEN = "/home/aipexws5/Jessica/Avion/mass-mcp/tests/fixtures/D150_simple.xml"


def test_unchanged_on_the_machine_it_was_written_on():
    text = f"Use CPACS file at {WRITTEN}\n"
    assert relocate_avion_paths(text, Path("/home/aipexws5/Jessica/Avion")) == text


def test_relocated_to_another_machines_checkout():
    out = relocate_avion_paths(f"Use CPACS file at {WRITTEN}\n", Path("/home/aipexws8/Jessica/Avion"))
    assert out == "Use CPACS file at /home/aipexws8/Jessica/Avion/mass-mcp/tests/fixtures/D150_simple.xml\n"


def test_only_the_checkout_part_changes():
    root = Path("/home/aipexws8/Jessica/Avion")
    assert relocate_avion_paths("no paths here", root) == "no paths here"
    assert relocate_avion_paths("/home/aipexws5/Jessica/Other/x.xml", root) == "/home/aipexws5/Jessica/Other/x.xml"
    assert relocate_avion_paths("/tmp/su2_session_x/config.cfg", root) == "/tmp/su2_session_x/config.cfg"


def test_the_sequential_prompts_name_a_file_that_exists_here():
    raw = AGENTS.read_text()
    assert WRITTEN in raw, "her YAML text is untouched"
    loaded = yaml.safe_dump(load_yaml(AGENTS), width=10_000)
    local = f"{AVION_ROOT}/mass-mcp/tests/fixtures/D150_simple.xml"
    assert local in loaded
    assert Path(local).is_file()
    if str(AVION_ROOT) != "/home/aipexws5/Jessica/Avion":
        assert WRITTEN not in loaded


def test_substitute_obj_relocates_nested_strings():
    out = substitute_obj({"a": [f"x {WRITTEN} y"]})
    assert out["a"][0] == f"x {AVION_ROOT}/mass-mcp/tests/fixtures/D150_simple.xml y"

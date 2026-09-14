"""B74: machine paths are derived from the checkout, not hardcoded.

The prompts must render byte-identically on the original machine (so results
already collected stay comparable) and must name a real file on any other.
"""

import subprocess
from pathlib import Path

import pytest
import yaml

from src.config import canonical
from src.config.canonical import AVION_ROOT, avion_path_prefix, substitute_obj

MAS = Path(__file__).resolve().parents[1]
ORIGINAL_ROOT = Path("/home/aipexws3/Jessica/Avion")
on_original_machine = pytest.mark.skipif(
    AVION_ROOT != ORIGINAL_ROOT, reason="byte-identity is only defined on the original machine"
)


def _git_show(rev_path: str) -> str | None:
    r = subprocess.run(["git", "-C", str(MAS), "show", rev_path], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def test_prefix_is_first_two_components():
    assert avion_path_prefix(Path("/home/aipexws3/Jessica/Avion")) == "/home/aipexws3"
    assert avion_path_prefix(Path("/home/jezemba/Avion")) == "/home/jezemba"


def test_fixture_named_in_task_exists():
    from scripts.stat_batch_runner import _D150_FIXTURE, _DEFAULT_MDO_F25_TASK

    assert _D150_FIXTURE.is_file()
    assert f"Use CPACS file at {_D150_FIXTURE}. " in _DEFAULT_MDO_F25_TASK


def test_staged_pipeline_renders_real_prefix_and_no_placeholder():
    raw = yaml.safe_load((MAS / "config" / "mdo_f25_staged_pipeline.yaml").read_text())
    rendered = yaml.safe_dump(substitute_obj(raw))
    assert "<<AVION_PATH_PREFIX>>" not in rendered
    assert f'"{avion_path_prefix()}/' in rendered


@on_original_machine
def test_task_text_byte_identical_to_pre_b74():
    from scripts.stat_batch_runner import _DEFAULT_MDO_F25_TASK

    old = (
        "Design a DLR-F25 class aircraft for minimum fuel burn. "
        "Use CPACS file at /home/aipexws3/Jessica/Avion/mass-mcp/tests/fixtures/D150_simple.xml. "
        "Target: 2500 nmi range, 239 passengers, Mach 0.78 cruise, 33000 ft altitude. "
        "Constraints: fuel_burned_kg <= 15000, gtow_kg <= 90000. "
        "Report the optimality gap versus the F25 reference (MTOM 85700 kg, fuel 12100 kg)."
    )
    assert _DEFAULT_MDO_F25_TASK == old


B74_BEFORE, B74_AFTER = "ed175ec", "fc9524b"


@on_original_machine
@pytest.mark.parametrize("cfg", sorted(p.name for p in (MAS / "config").glob("*.yaml")))
def test_b74_commit_rendered_every_config_identically(cfg):
    """The B74 commit itself changed no rendered prompt on the original machine.

    Compares the two historical revisions rather than the working tree, so later
    INTENTIONAL prompt edits (e.g. the 2026-09-14 create_session wording) do not
    masquerade as a B74 regression.
    """
    before = _git_show(f"{B74_BEFORE}:config/{cfg}")
    after = _git_show(f"{B74_AFTER}:config/{cfg}")
    if before is None or after is None:
        pytest.skip(f"{cfg} not present at both {B74_BEFORE} and {B74_AFTER}")
    assert substitute_obj(yaml.safe_load(after)) == substitute_obj(yaml.safe_load(before))


def test_other_machine_gets_its_own_prefix(monkeypatch):
    other = Path("/home/jezemba/Avion")
    monkeypatch.setattr(canonical, "avion_path_prefix", lambda root=other: avion_path_prefix(other))
    canonical.render_snippets.cache_clear()
    try:
        raw = yaml.safe_load((MAS / "config" / "mdo_f25_staged_pipeline.yaml").read_text())
        rendered = yaml.safe_dump(substitute_obj(raw))
        assert '"/home/jezemba/' in rendered
        assert "aipexws3" not in rendered
    finally:
        canonical.render_snippets.cache_clear()

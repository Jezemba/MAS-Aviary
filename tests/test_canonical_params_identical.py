"""Experimental-design invariant: every combo must resolve to byte-identical
discipline parameters, so the ONLY variable across combos is the coordination
structure. Discipline params come from config/mdo_f25_canonical_baseline.yaml via
<<PLACEHOLDER>> substitution; this test fails if any combo hardcodes a divergent
value or if substitution yields different text.
"""
import re
from pathlib import Path

import pytest

import sys
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.config.loader import load_yaml  # noqa: E402
from src.config.canonical import render_snippets, substitute_text  # noqa: E402

COMBO_CONFIGS = [
    "config/mdo_f25_sequential_agents.yaml",
    "config/mdo_f25_graph.yaml",
    "config/mdo_f25_staged_pipeline.yaml",
    "config/mdo_f25_orchestrated_agents.yaml",
    "config/mdo_f25_networked_agents.yaml",
]

# Discipline params that must NOT appear hardcoded in a combo config — they must
# come from the canonical baseline via placeholders.
HARDCODED_SU2_FORBIDDEN = ['"CFL_NUMBER"', '"MGLEVEL"', '"CONV_RESIDUAL_MINVAL"', '"JST_SENSOR_COEFF"']

# Divergent literal values that must be gone (replaced by placeholders) across ALL combos.
HARDCODED_DIVERGENT_FORBIDDEN = [
    'far_field_distance=50',
    'far_field_distance=10.0',
    'range_nmi=2500',
    'cruise_mach=0.78',
    'cruise_altitude_ft=33000',
    'wing_mass_method="both"',
    'wing_mass_method="flops"',
    'material="composite"',
    'material="aluminum"',
    'component_uid="Wing"',
    'component_uid="Wing1"',
    'component_uid="Fuselage"',
]

# Granular placeholders and their expected canonical rendering.
EXPECTED_RENDER = {
    "WING_UID": "Wing1",
    "FUSELAGE_UID": "Fuselage1",
    "FAR_FIELD_DISTANCE": "10.0",
    "WING_MASS_METHOD": "flops",
    "CRUISE_MACH": "0.785",
    "CRUISE_ALTITUDE_FT": "35000",
    "RANGE_NMI": "1500",
    "BURNER_T4_K": "1587",
}


def _text(path: str) -> str:
    return (_ROOT / path).read_text()


@pytest.mark.parametrize("cfg", COMBO_CONFIGS)
def test_uses_su2_placeholder_not_hardcoded(cfg):
    """Each combo references <<SU2_CONFIG>> and hardcodes no SU2 numerics."""
    raw = _text(cfg)
    assert "<<SU2_CONFIG>>" in raw, f"{cfg} does not reference <<SU2_CONFIG>>"
    for token in HARDCODED_SU2_FORBIDDEN:
        assert token not in raw, f"{cfg} still hardcodes SU2 param {token}"


def test_su2_config_renders_identically_across_combos():
    """After canonical substitution, the SU2 dict is byte-identical everywhere."""
    su2 = render_snippets()["SU2_CONFIG"]
    # It must be a well-formed dict literal with the CFL fix and multigrid.
    assert su2.startswith("{") and su2.endswith("}")
    assert '"CFL_NUMBER": 20' in su2
    assert '"MGLEVEL": 3' in su2
    assert '"CONV_FIELD": "RMS_DENSITY"' in su2
    # Substituting the same placeholder in every combo yields the same text.
    rendered = {cfg: substitute_text("<<SU2_CONFIG>>") for cfg in COMBO_CONFIGS}
    assert len(set(rendered.values())) == 1, "SU2_CONFIG rendered differently across combos"


@pytest.mark.parametrize("cfg", COMBO_CONFIGS)
def test_no_hardcoded_divergent_values(cfg):
    """No combo may hardcode a discipline value that must come from the canonical file."""
    raw = _text(cfg)
    for token in HARDCODED_DIVERGENT_FORBIDDEN:
        assert token not in raw, f"{cfg} still hardcodes divergent value: {token}"


def test_granular_placeholders_render_to_canonical():
    """Each granular placeholder renders to the finalized canonical value."""
    s = render_snippets()
    for name, expected in EXPECTED_RENDER.items():
        assert s[name] == expected, f"{name} rendered {s[name]!r}, expected {expected!r}"


@pytest.mark.parametrize("cfg", COMBO_CONFIGS)
def test_no_unresolved_placeholders_after_load(cfg):
    """load_yaml must resolve every <<...>>; none may survive into the runtime config."""
    loaded = load_yaml(cfg)

    def walk(o):
        if isinstance(o, str):
            return re.findall(r"<<[A-Z0-9_]+>>", o)
        if isinstance(o, dict):
            return [p for v in o.values() for p in walk(v)]
        if isinstance(o, list):
            return [p for v in o for p in walk(v)]
        return []

    residual = walk(loaded)
    assert not residual, f"{cfg} has unresolved placeholders after load: {set(residual)}"

"""W3 (validation 2, O_GR it2): the recommendation keyword table routes to the discipline that can change the variable.

O_GR it2's integrator recommended "Reduce drag coefficient or optimize lift-to-drag ratio" to the aerodynamics analyst;
the table had no drag / L-D word, so it fell back on the addressed role and went to AERO at MODERATE. Under the drag
model decision (B324) the mission flies aviary's FLOPS drag of the geometry, SU2 is recorded only, and the aero analyst
has no geometry tools: a dead end that spent the link's budget. The same holds for the structures analyst and "reduce
wing / structural mass" (mass method, material and load factor are the study's fixed controls; the mass follows the
geometry). Fixture: the 148 distinct recommendations recorded in design.json (final_val2, final_val, l20_final), with
the node graph_router at 0fde82e chose (routed_before). Pure function tests, no I/O beyond the fixture.
"""
import json
import re
from pathlib import Path

import pytest

from src.coordination import graph_router as gr

RECS = json.loads((Path(__file__).parent / "fixtures" / "w3_recorded_recommendations.json").read_text())["recommendations"]
# A recommendation whose routing W3 changes names a flown aero quantity or a structural mass; nothing else moves.
_MOVED = re.compile(r"\bdrag\b|lift[ _-]?to[ _-]?drag|\bCL\b|\bCD\b|CL_CRUISE|CD_CRUISE|lift coefficient|\bmass\b|"
                    r"structur|lightweight|wing[ _-]?(design|shape)", re.I)


def _target(rec):
    return gr.recommendation_target({"RECOMMENDATION_TO": rec["recommendation_to"],
                                     "RECOMMENDED_CHANGE": rec["recommended_change"]})


def test_w3_o_gr_it2_drag_recommendation_goes_to_geometry():
    rec = {"recommendation_to": "aerodynamics_analyst",
           "recommended_change": "Reduce drag coefficient or optimize lift-to-drag ratio"}
    assert any(r["recommended_change"] == rec["recommended_change"] and r["routed_before"] == gr.AERO for r in RECS)
    assert _target(rec) == gr.GEOMETRY
    d = gr.route_after_review("", gr.MODERATE, recorded={"verdict": "CONTINUE", **rec})
    assert (d.next_node, d.level, d.rule) == (gr.GEOMETRY, gr.COMPLEX, "13")


def test_w3_o_gr_it2_lightweighting_goes_to_geometry():
    rec = {"recommendation_to": "structures_analyst",
           "recommended_change": "Explore lightweighting opportunities to reduce GTO"}
    assert _target(rec) == gr.GEOMETRY


@pytest.mark.parametrize("change", ["Reduce drag", "improve L/D", "raise the L_OVER_D", "cut induced drag",
                                    "reduce the wetted area", "lower CD_CRUISE", "lower the cruise CL",
                                    "reduce wing mass", "reduce OEM", "reduce structural weight",
                                    "optimise the wing shape"])
def test_w3_drag_lift_and_structural_mass_words_route_to_geometry(change):
    for to in ("aerodynamics_analyst", "structures_analyst", "mission_architect", ""):
        assert _target({"recommendation_to": to, "recommended_change": change}) == gr.GEOMETRY, (to, change)


@pytest.mark.parametrize("change,node", [
    ("Re-run SU2 on a finer mesh", gr.AERO), ("trim SU2 to the target CL", gr.AERO),
    ("Investigate the discrepancy between SU2 and Aviary aerodynamic results (CL=0.558 vs 0.948)", gr.AERO),
    ("re-run estimate_mass on the exported CPACS", gr.MASS), ("switch the material to composite", gr.MASS),
    ("Increase engine bypass ratio from 5.105 to 6.0", gr.PROPULSION), ("Reduce SFC_CRUISE from 0.64 to 0.63", gr.PROPULSION),
    ("Increase engine thrust or reduce aircraft weight", gr.PROPULSION),
    ("Increase wing aspect ratio to cut drag", gr.GEOMETRY),
    ("Reduce GTOW by adjusting cruise altitude or Mach number", gr.MISSION),
])
def test_w3_procedures_and_owners_unchanged(change, node):
    assert _target({"recommendation_to": "mdo_integrator", "recommended_change": change}) == node


def test_w3_recorded_recommendations_before_and_after():
    moved = [r for r in RECS if r["routed_before"] != r["routed_after"]]
    assert len(RECS) == 148 and len(moved) == 35
    for r in RECS:
        assert _target(r) == r["routed_after"], r
        if r["routed_before"] != r["routed_after"]:
            # only flown-aero / structural-mass wording moves, and only to the geometry
            assert r["routed_after"] == gr.GEOMETRY and _MOVED.search(r["recommended_change"]), r
            assert r["routed_before"] in (gr.AERO, gr.MASS), r
        elif not _MOVED.search(r["recommended_change"]):
            assert r["routed_after"] == r["routed_before"], r


def test_w3_aero_and_mass_only_get_what_they_can_do():
    for r in RECS:
        if r["routed_after"] == gr.AERO:
            assert re.search(r"SU2|CFD|mesh|trim|angle of attack|AOA", r["recommended_change"], re.I), r
        if r["routed_after"] == gr.MASS:
            assert re.search(r"estimat|material|composite|alumin|mass[ _-]?mcp", r["recommended_change"], re.I), r


def test_w3_networked_board_uses_the_same_table():
    from src.coordination import board_graph

    assert board_graph.gr is gr          # the networked board routes the integrator's recommendation with this table

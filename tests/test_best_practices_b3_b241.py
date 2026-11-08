"""read_best_practices serves the B3 engine-deck and B241 mesh-deformation lessons (2026-11-08)."""
from __future__ import annotations

import re

from src.tools.best_practices_tool import ReadBestPractices, read_sections


def test_propulsion_section_has_engine_deck_and_ratings():
    text = read_sections("propulsion")
    for needle in ("engine deck", "T4_TAKEOFF", "T4_CLIMB", "3640", "DeckNotUsable", "engine_mass_kg",
                   "do not call them"):
        assert needle in text, needle


def test_geometry_section_has_mesh_deformation():
    text = read_sections("geometry")
    for needle in ("deformed_from_anchor", "MeshDeformationRefused:", "MeshDeformationToolFailure:",
                   "mesh_settings_ignored", "surface_flipped_area_m2", "junction_overhang_nodes"):
        assert needle in text, needle


def test_aero_and_simulation_sections_updated():
    assert "same baseline mesh" in read_sections("aero")
    sim = read_sections("simulation")
    assert "external_deck" in sim and "engine.deck" in sim


def test_general_notes_reach_every_discipline():
    for d in ("geometry", "aero", "propulsion", "mission", "simulation", "integrator"):
        assert "Two couplings are done for you" in read_sections(d), d


def test_entries_newest_last_in_every_section():
    text = read_sections()
    for part in re.split(r"^## ", text, flags=re.M)[1:]:
        dates = re.findall(r"^- \[(\d{4}-\d{2}-\d{2})", part, flags=re.M)
        assert dates == sorted(dates), part.split("\n", 1)[0]


def test_tool_returns_the_section():
    assert "T4_TAKEOFF" in ReadBestPractices().forward("propulsion")


def test_b247_climb_start_and_thrust_message_are_in_simulation_section():
    from pathlib import Path

    text = Path("config/best_practices.md").read_text(encoding="utf-8")
    section = text.split("## simulation")[1].split("\n## ")[0]
    assert "1.2 x the clean (flaps-up) stall speed" in section
    assert "failure.thrust_limited" in section

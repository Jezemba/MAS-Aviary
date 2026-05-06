#!/usr/bin/env python3
"""Validates DesignState consistency for the aircraft-design-mdo skill.

Checks that:
1. All required MCP sessions are present
2. Required results are populated
3. Constraints are evaluable
4. History has expected fields
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from the skill scripts/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.coordination.design_state import DesignState

REQUIRED_SESSIONS = ["tigl", "su2", "mass", "pycycle", "aviary"]
REQUIRED_RESULTS = ["fuel_burned_kg", "gross_mass_kg", "oem_kg"]
REQUIRED_CONSTRAINTS = ["range_nm", "tofl_m"]


def validate(state: DesignState) -> list[str]:
    """Validate a DesignState and return a list of issues (empty = valid)."""
    issues = []

    # Check sessions.
    for name in REQUIRED_SESSIONS:
        if not state.get_session(name):
            issues.append(f"Missing session for MCP: {name}")

    # Check CPACS file.
    if not state.cpacs_file_path:
        issues.append("No CPACS file path set")

    # Check results.
    for key in REQUIRED_RESULTS:
        val = state.get_result(key)
        if val is None:
            issues.append(f"Missing result: {key}")
        elif val <= 0:
            issues.append(f"Invalid result {key}: {val} (must be positive)")

    # Check constraints.
    for name in REQUIRED_CONSTRAINTS:
        if name not in state.constraints:
            issues.append(f"Missing constraint: {name}")

    # Check history.
    if state.iteration > 0 and not state.history:
        issues.append(f"Iteration is {state.iteration} but history is empty")

    return issues


def main():
    """Example usage with a test DesignState."""
    ds = DesignState(cpacs_file_path="/tmp/D150_simple.xml")
    ds.set_session("aviary", "test-uuid")
    ds.set_result("fuel_burned_kg", 12100.0)
    ds.set_result("gross_mass_kg", 85700.0)
    ds.set_result("oem_kg", 46300.0)
    ds.set_constraint("range_nm", value=2500.0, limit=2500.0, operator=">=")
    ds.set_constraint("tofl_m", value=2100.0, limit=2200.0, operator="<=")

    issues = validate(ds)
    if issues:
        print("Validation issues:")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    else:
        print("DesignState is valid.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

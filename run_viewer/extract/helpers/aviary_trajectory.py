"""Dump the flown trajectory from an aviary recorder (problem_history.db) to JSON.

Runs in the aviary env (needs openmdao). Reads the recorder's "final" case, which is the
last simulation run in that aviary session. Usage:

    <aviary-env>/bin/python -I aviary_trajectory.py <problem_history.db> <out.json>
"""

import json
import sys

import numpy as np
import openmdao.api as om

# timeseries name -> (output key, target unit, factor from the recorded unit)
FIELDS = {
    "time": ("time_s", "s", 1.0),
    "altitude": ("altitude_ft", "ft", 1.0),
    "mach": ("mach", "", 1.0),
    "mass": ("mass_kg", "kg", 1.0),
    "distance": ("distance_nmi", "nmi", 1.0 / 1852.0),
    "throttle": ("throttle", "", 1.0),
    "drag": ("drag_lbf", "lbf", 1.0),
    "thrust_net_total": ("thrust_lbf", "lbf", 1.0),
}
EXPECTED_UNITS = {"time": "s", "altitude": "ft", "mass": "kg", "distance": "m", "drag": "lbf",
                  "thrust_net_total": "lbf"}


def main(db_path: str, out_path: str) -> None:
    case = om.CaseReader(db_path).get_case("final")
    units = {meta["prom_name"]: meta.get("units")
             for _, meta in case.list_outputs(units=True, prom_name=True, out_stream=None)}
    phases = []
    for phase in ("climb", "cruise", "descent"):
        row = {"name": phase}
        for ts, (key, _, factor) in FIELDS.items():
            prom = f"traj.{phase}.timeseries.{ts}"
            if prom not in units:
                continue
            expected = EXPECTED_UNITS.get(ts)
            if expected and units[prom] != expected:
                raise ValueError(f"{prom} recorded in {units[prom]}, expected {expected}")
            row[key] = [round(float(v), 4) for v in np.asarray(case.outputs[prom]).ravel() * factor]
        if len(row) > 1:
            phases.append(row)
    with open(out_path, "w") as f:
        json.dump({"source": "aviary problem_history.db (final case)", "phases": phases}, f)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

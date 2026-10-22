"""Re-fly a link's mission with aviary-mcp's own runner, offline, from the recorded inputs.

Runs in the aviary env with aviary-mcp on sys.path; no MCP server is contacted. The recorder
lands in <workdir>/aviary_<name>_out/problem_history.db (aviary writes relative to cwd).

    cd <workdir> && <aviary-env>/bin/python replay_aviary.py <aviary-mcp dir> <inputs.json> <name> <out.json>

inputs.json: {"aircraft_params": {...}, "mission_config": {...}} as recorded by get_results.
"""

import json
import sys


def main(aviary_mcp: str, inputs_path: str, name: str, out_path: str) -> None:
    sys.path.insert(0, aviary_mcp)
    from aviary_runner import create_aviary_problem, run_problem

    inputs = json.load(open(inputs_path))
    prob = create_aviary_problem(aircraft_params=inputs["aircraft_params"],
                                 mission_config=inputs["mission_config"], name=f"aviary_{name}")
    run = run_problem(prob, timeout_seconds=inputs.get("timeout_seconds", 900))
    with open(out_path, "w") as f:
        json.dump(run, f, default=str)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])

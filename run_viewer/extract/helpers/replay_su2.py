"""Re-run a link's SU2 solve with su2-mcp's own functions, offline, from the recorded inputs.

Runs in su2-mcp's venv and imports the tools as a library: no MCP server is contacted. The
session workdir is made under $TMPDIR (set it to a folder of your own) and is kept.
Rank count comes from SU2_MPI_RANKS (su2_mcp/mpi.py).

    <su2-venv>/bin/python -I replay_su2.py <plan.json> <out.json>

plan.json: {"mesh": path, "create": {...create_su2_session inputs without initial_mesh...},
            "configure": [{...configure_from_cpacs inputs...}], "updates": [{...}], "solver": {...}}
"""

import base64
import json
import sys

from su2_mcp.tools.config_tools import configure_from_cpacs, update_config_entries
from su2_mcp.tools.run_tools import run_su2_solver
from su2_mcp.tools.session import create_su2_session


def _check(step: str, out: dict) -> dict:
    if isinstance(out, dict) and out.get("error"):
        raise RuntimeError(f"{step}: {out['error']}")
    return out


def main(plan_path: str, out_path: str) -> None:
    plan = json.load(open(plan_path))
    with open(plan["mesh"], "rb") as f:
        mesh_b64 = base64.b64encode(f.read()).decode()
    created = _check("create_su2_session", create_su2_session(initial_mesh=mesh_b64, **plan["create"]))
    sid = created["session_id"]
    for kwargs in plan.get("configure", []):
        _check("configure_from_cpacs", configure_from_cpacs(session_id=sid, **kwargs))
    for kwargs in plan.get("updates", []):
        _check("update_config_entries", update_config_entries(session_id=sid, **kwargs))
    solved = _check("run_su2_solver", run_su2_solver(session_id=sid, **plan.get("solver", {})))
    with open(out_path, "w") as f:
        json.dump({"workdir": created["workdir"], "solve": solved}, f, default=str)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

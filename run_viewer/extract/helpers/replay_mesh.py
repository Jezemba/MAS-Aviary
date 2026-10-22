"""Re-make a link's SU2 volume mesh with tigl-mcp's own generate_volume_mesh, offline.

Runs in the tigl env and imports the tool as a library: no MCP server is contacted.

    <tigl-env>/bin/python -I replay_mesh.py <aircraft.xml> <mesh params json> <out mesh.su2>

The mesh params are the recorded generate_volume_mesh inputs (session_id is replaced).
"""

import base64
import json
import sys

from tigl_mcp.session_manager import SessionManager
from tigl_mcp.tools.cpacs_io import open_cpacs_tool
from tigl_mcp.tools.volume_mesh import generate_volume_mesh_tool


def main(cpacs: str, params_json: str, out_mesh: str) -> None:
    sessions = SessionManager()
    opened = open_cpacs_tool(sessions).handler({"source_type": "path", "source": cpacs})
    params = dict(json.loads(params_json))
    params["session_id"] = opened["session_id"]
    result = generate_volume_mesh_tool(sessions).handler(params)
    with open(out_mesh, "wb") as f:
        f.write(base64.b64decode(result["mesh_base64"]))
    print(json.dumps({"mesh_size_bytes": result["mesh_size_bytes"], "statistics": result.get("statistics")}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])

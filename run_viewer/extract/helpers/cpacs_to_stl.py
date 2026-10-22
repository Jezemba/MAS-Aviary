"""Triangulate a CPACS aircraft with TiGL and write an STL.

Runs in the tigl env. Usage:

    <tigl-env>/bin/python -I cpacs_to_stl.py <aircraft.xml> <out.stl> [deflection]
"""

import sys

from tigl3 import tigl3wrapper
from tixi3 import tixi3wrapper


def main(cpacs: str, out_stl: str, deflection: float = 0.05) -> None:
    tixi = tixi3wrapper.Tixi3()
    tixi.open(cpacs)
    tigl = tigl3wrapper.Tigl3()
    tigl.open(tixi, "")
    try:
        tigl.exportMeshedGeometrySTL(out_stl, deflection)
    finally:
        tigl.close()
        tixi.close()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 0.05)

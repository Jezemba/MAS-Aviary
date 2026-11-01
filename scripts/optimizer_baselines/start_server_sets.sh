#!/usr/bin/env bash
# One whole MCP server set (tigl, su2, pycycle, aviary, mass) per optimizer worker, at base + 10*worker + slot
# (slots: tigl 0, su2 1, pycycle 2, aviary 3, mass 4; run_baseline.py --server-base). tigl blocks while meshing
# and su2 while solving, so parallel workers cannot share a server. CPU only; nothing is ever written to the
# study's servers. Usage: start_server_sets.sh <base> <workers>   e.g. start_server_sets.sh 10000 6
set -euo pipefail
BASE=$1; N=$2
# The machine's own environment for the Avion servers (aipexws8: ~/Jessica/avion_env.sh -- PYTHONNOUSERSITE,
# XDG cache/config dirs, ...). Without it mass-mcp could not build any mass model (run 5, 2026-10-22: sqlite
# "attempt to write a readonly database", mTOM 0 even for the baseline fixture).
ENVFILE=${AVION_ENV_FILE:-$HOME/Jessica/avion_env.sh}
# shellcheck disable=SC1090
[ -f "$ENVFILE" ] && source "$ENVFILE"
AVION=${AVION:-$HOME/Jessica/Avion}; ENVS=${AVION_CONDA_ENVS:-$HOME/Jessica/avion_envs}
# aviary-mcp checkout to serve (optimizer budget runs: feat/optimizer-mission-residuals, Avion B236).
AVIARY_MCP_DIR=${AVIARY_MCP_DIR:-$AVION/aviary-mcp}
LOGS=${LOGS:-$AVION/MAS-Aviary-opt/logs/baselines/mcp}; mkdir -p "$LOGS"
export TMPDIR=${BASELINE_TMPDIR:-$HOME/Jessica/tmp_baselines}; mkdir -p "$TMPDIR"
# Every server runs in its OWN working folder. aviary (inside mass-mcp, and aviary-mcp) and pycycle write their
# OpenMDAO output -- including a sqlite recorder -- to <problem>_out folders in the working folder, named by a
# per-process counter, so servers sharing a folder write the SAME sqlite files (run 6, 2026-10-22: "attempt to
# write a readonly database", every mass 0). Separate folders also keep them away from the study's servers.
WORK=${BASELINE_WORKDIR:-$TMPDIR/sets}
start() { local name=$1 port=$2; shift 2
  if ss -ltn | grep -q ":$port "; then echo "port $port taken; not starting $name" >&2; return; fi
  mkdir -p "$WORK/${name}_${port}"
  (cd "$WORK/${name}_${port}" && setsid nohup env CUDA_VISIBLE_DEVICES="" TMPDIR="$TMPDIR" "$@" \
     > "$LOGS/${name}_${port}.log" 2>&1 < /dev/null &)
}
cd "$AVION"
for ((w = 0; w < N; w++)); do
  P=$((BASE + 10 * w))
  start tigl $((P + 0)) "$ENVS/tigl-env/bin/tigl-mcp" --transport streamable-http --host 127.0.0.1 --port $((P + 0))
  # Fixed ranks and no core binding: 18 parallel MPI jobs each bind to the SAME first cores by default
  # (run 4, 2026-10-22: 54 ranks on ~3 cores, 1.7% CPU each, 22 SU2 iterations in 30 min).
  start su2 $((P + 1)) env PATH="$ENVS/su2env/bin:$PATH" SU2_MPI_RANKS="${SU2_MPI_RANKS:-3}" \
    SU2_MPIRUN_ARGS="${SU2_MPIRUN_ARGS:---mca osc ^ucx --bind-to none}" \
    "$AVION/su2-mcp/.venv/bin/su2-mcp" --transport streamable-http --host 127.0.0.1 --port $((P + 1))
  start pycycle $((P + 2)) "$AVION/pycycle-mcp/.venv/bin/pycycle-mcp" --transport streamable-http --host 127.0.0.1 --port $((P + 2))
  start aviary $((P + 3)) env AVIARY_MCP_HOST=127.0.0.1 AVIARY_MCP_PORT=$((P + 3)) \
    "$ENVS/aviary/bin/python" "$AVIARY_MCP_DIR/aviary_mcp_server.py"
  start mass $((P + 4)) "$ENVS/mass-tigl/bin/mass-mcp-server" --transport streamable-http --host 127.0.0.1 --port $((P + 4))
done
echo "started $N server sets at base $BASE"

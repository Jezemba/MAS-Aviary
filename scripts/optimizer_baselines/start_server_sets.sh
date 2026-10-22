#!/usr/bin/env bash
# One whole MCP server set (tigl, su2, pycycle, aviary, mass) per optimizer worker, at base + 10*worker + slot
# (slots: tigl 0, su2 1, pycycle 2, aviary 3, mass 4; run_baseline.py --server-base). tigl blocks while meshing
# and su2 while solving, so parallel workers cannot share a server. CPU only; nothing is ever written to the
# study's servers. Usage: start_server_sets.sh <base> <workers>   e.g. start_server_sets.sh 10000 6
set -euo pipefail
BASE=$1; N=$2
AVION=${AVION:-$HOME/Jessica/Avion}; ENVS=${AVION_CONDA_ENVS:-$HOME/Jessica/avion_envs}
LOGS=${LOGS:-$AVION/MAS-Aviary-opt/logs/baselines/mcp}; mkdir -p "$LOGS"
export TMPDIR=${BASELINE_TMPDIR:-$HOME/Jessica/tmp_baselines}; mkdir -p "$TMPDIR"
start() { local name=$1 port=$2; shift 2
  if ss -ltn | grep -q ":$port "; then echo "port $port taken; not starting $name" >&2; return; fi
  setsid nohup env CUDA_VISIBLE_DEVICES="" TMPDIR="$TMPDIR" "$@" > "$LOGS/${name}_${port}.log" 2>&1 < /dev/null &
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
  (cd "$AVION/aviary-mcp" && start aviary $((P + 3)) env AVIARY_MCP_HOST=127.0.0.1 AVIARY_MCP_PORT=$((P + 3)) \
    "$ENVS/aviary/bin/python" aviary_mcp_server.py)
  start mass $((P + 4)) "$ENVS/mass-tigl/bin/mass-mcp-server" --transport streamable-http --host 127.0.0.1 --port $((P + 4))
done
echo "started $N server sets at base $BASE"

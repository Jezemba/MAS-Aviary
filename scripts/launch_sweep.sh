#!/usr/bin/env bash
# Launch a sweep ONLINE with the correct W&B key.
#
# Why this exists: on 2026-08-16 three key sources disagreed --
#   Avion/.env      fingerprint 3d2a6c48   <- the key Jessica updated, the good one
#   shell env       fingerprint 5aa6b694   <- stale
#   ~/.netrc        fingerprint 5aa6b694   <- stale
# Anything relying on the ambient environment picks up the stale key and fails
# auth, which is why runs were being launched WANDB_MODE=offline in the first
# place. This script always sources the key from Avion/.env and exports it so it
# wins over ~/.netrc, rather than trusting whatever is cached locally.
#
# Usage:
#   scripts/launch_sweep.sh <log-tag> --repeats N --combinations A B --timeout M
# e.g.
#   scripts/launch_sweep.sh budget_test --repeats 2 \
#       --combinations mdo_f25_orchestrated_graph_routed --timeout 180

set -euo pipefail

MAS=/home/aipexws3/Jessica/Avion/MAS-Aviary
ENV_FILE=/home/aipexws3/Jessica/Avion/.env
cd "$MAS"

if [ $# -lt 1 ]; then
    echo "usage: $0 <log-tag> [runner args...]" >&2
    exit 2
fi
TAG=$1; shift

KEY=$(grep -E "^WANDB_API_KEY=" "$ENV_FILE" | sed 's/^WANDB_API_KEY=//; s/^"//; s/"$//')
if [ -z "$KEY" ]; then
    echo "ERROR: no WANDB_API_KEY in $ENV_FILE" >&2
    exit 1
fi
echo "using W&B key from $ENV_FILE (fingerprint $(printf %s "$KEY" | sha256sum | cut -c1-8))"

# Fail fast on a bad key rather than losing a multi-hour run to an auth error
# mid-flight -- the reason offline mode was the safe default before.
if ! WANDB_API_KEY="$KEY" ./../.venv/bin/python -c "
import os, sys, wandb
try:
    wandb.Api(api_key=os.environ['WANDB_API_KEY']).default_entity
except Exception as e:
    print('W&B auth FAILED:', e); sys.exit(1)
" ; then
    echo "Refusing to launch online with a key that does not authenticate." >&2
    echo "Fix the key in $ENV_FILE, or launch with WANDB_MODE=offline and sync later." >&2
    exit 1
fi

LOG="logs/${TAG}_$(date +%Y%m%d_%H%M%S).log"
setsid nohup env \
    PYTHONPATH="$MAS" \
    WANDB_API_KEY="$KEY" \
    WANDB_MODE=online \
    ../.venv/bin/python scripts/stat_batch_runner.py "$@" \
    > "$LOG" 2>&1 < /dev/null &
disown

sleep 20
echo "log=$LOG"
ps -o pid,pgid,etime,cmd -C python 2>/dev/null | grep stat_batch | head -2
echo
echo "To stop it, kill the process GROUP, not the parent:"
echo "  kill -TERM -\$(ps -o pgid= -p <pid> | tr -d ' ')"
echo "A bare kill -9 on the parent orphans the GPU child (cost 29 GB for 13 min once)."

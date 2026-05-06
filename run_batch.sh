#!/bin/bash
cd /home/aipexws3/Jessica/Avion/MAS-Aviary
export PYTHONPATH="/home/aipexws3/Jessica/Avion/MAS-Aviary:$PYTHONPATH"

# Load secrets from .env (gitignored). See .env.example for required vars.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export PATH="/home/aipexws3/anaconda3/envs/su2env/bin:$PATH"
export LD_LIBRARY_PATH="/home/aipexws3/anaconda3/envs/tigl-env/lib:$LD_LIBRARY_PATH"
exec /home/aipexws3/Jessica/Avion/.venv/bin/python -u scripts/stat_batch_runner.py "$@"

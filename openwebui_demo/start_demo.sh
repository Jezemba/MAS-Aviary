#!/usr/bin/env bash
# Start the MAS-Aviary Open WebUI demo end-to-end.
#
# 1. Verify the .env (Anthropic + WandB keys) exists in MAS-Aviary/
#    — required only for the "mas-aviary-live" model, but always loaded
#    so chat_server.py can pass it through to subprocesses.
# 2. Start chat_server.py on host port 8090 (background).
# 3. Bring up Open WebUI via docker-compose on host port 3000.
# 4. Wait until both are responsive, then print the URL.
#
# Stop the demo with ./stop_demo.sh (or Ctrl+C + docker-compose down).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
VENV_PY="$REPO/../.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "ERR: expected venv python at $VENV_PY — adjust path or create the venv." >&2
  exit 1
fi

if [[ ! -f "$REPO/.env" ]]; then
  echo "Note: $REPO/.env not found." \
       "Live mode will skip Anthropic. Replay mode still works." >&2
fi

# Step 1: kill any stale chat_server.py from a previous demo
pkill -f "openwebui_demo/chat_server.py" 2>/dev/null || true
sleep 0.5

# Step 2: spawn chat_server.py
echo "[demo] starting chat_server.py on :8090..."
cd "$HERE"
nohup "$VENV_PY" chat_server.py > /tmp/mas_aviary_chat_server.log 2>&1 &
CHAT_PID=$!
echo "$CHAT_PID" > /tmp/mas_aviary_chat_server.pid
echo "[demo] chat_server pid=$CHAT_PID, logging to /tmp/mas_aviary_chat_server.log"

# Step 3: wait until it's healthy
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8090/healthz >/dev/null 2>&1; then
    echo "[demo] chat_server is up."
    break
  fi
  sleep 0.5
done
if ! curl -fsS http://127.0.0.1:8090/healthz >/dev/null 2>&1; then
  echo "ERR: chat_server failed to come up. Tail of log:" >&2
  tail -20 /tmp/mas_aviary_chat_server.log >&2
  exit 1
fi

# Step 4: docker-compose up
echo "[demo] bringing up Open WebUI via docker-compose..."
cd "$HERE"
docker-compose up -d

# Step 5: wait for Open WebUI to respond
for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:3000/ >/dev/null 2>&1; then
    echo "[demo] Open WebUI is up."
    break
  fi
  sleep 1
done

echo
echo "=============================================================="
echo "  MAS-Aviary MDO Demo is ready."
echo
echo "  Open WebUI:    http://127.0.0.1:3000"
echo "  Chat server:   http://127.0.0.1:8090/v1  (API)"
echo "  Models:        mas-aviary-replay  (default, ~30 s)"
echo "                 mas-aviary-live    (real run, ~10 min)"
echo
echo "  Stop with:     ./stop_demo.sh"
echo "=============================================================="

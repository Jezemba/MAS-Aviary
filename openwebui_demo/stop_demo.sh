#!/usr/bin/env bash
# Tear down the MAS-Aviary Open WebUI demo cleanly.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

echo "[stop] docker-compose down..."
docker-compose down

if [[ -f /tmp/mas_aviary_chat_server.pid ]]; then
  PID="$(cat /tmp/mas_aviary_chat_server.pid)"
  if kill -0 "$PID" 2>/dev/null; then
    echo "[stop] killing chat_server pid=$PID"
    kill "$PID" 2>/dev/null || true
    sleep 1
    kill -9 "$PID" 2>/dev/null || true
  fi
  rm -f /tmp/mas_aviary_chat_server.pid
fi

pkill -f "openwebui_demo/chat_server.py" 2>/dev/null || true
echo "[stop] done."

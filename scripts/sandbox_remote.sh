#!/usr/bin/env bash
# Run the stage sandbox on the GPU machine and mirror its folder here, so report.html can be opened locally.
#
#   scripts/sandbox_remote.sh --stages propulsion_analyst --seed-from logs/stat_results/<run>/repeat_000/<combo>
#
# Arguments are passed to scripts/stage_sandbox.py unchanged (paths are on the remote machine). The folder
# is copied to logs/sandbox/ here every 5 s until the sandbox finishes; open logs/sandbox/<name>/report.html.
set -euo pipefail
HOST="${SANDBOX_HOST:-aipexws5@172.26.37.4}"
KEY="${SANDBOX_KEY:-$HOME/.ssh/voir_shard}"
RDIR="${SANDBOX_REMOTE_DIR:-Jessica/Avion/MAS-Aviary}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH=(ssh -o BatchMode=yes -i "$KEY" "$HOST")
NAME="$(date +%Y%m%d-%H%M%S)_sandbox"
QARGS=$(printf ' %q' "$@")
"${SSH[@]}" "source ~/avion_env.sh >/dev/null 2>&1; cd ~/$RDIR && mkdir -p logs/sandbox && setsid nohup env PYTHONPATH=\$PWD \
  ../.venv/bin/python scripts/stage_sandbox.py --out logs/sandbox/$NAME$QARGS > logs/sandbox/$NAME.log 2>&1 < /dev/null & disown"
echo "started on $HOST: ~/$RDIR/logs/sandbox/$NAME   (console log: logs/sandbox/$NAME.log)"
mkdir -p "$HERE/logs/sandbox/$NAME"
echo "open: $HERE/logs/sandbox/$NAME/report.html"
while true; do
  rsync -a -e "ssh -o BatchMode=yes -i $KEY" "$HOST:~/$RDIR/logs/sandbox/$NAME/" "$HERE/logs/sandbox/$NAME/" 2>/dev/null || true
  rsync -a -e "ssh -o BatchMode=yes -i $KEY" "$HOST:~/$RDIR/logs/sandbox/$NAME.log" "$HERE/logs/sandbox/" 2>/dev/null || true
  if grep -q '"type": "done"' "$HERE/logs/sandbox/$NAME/events.jsonl" 2>/dev/null; then echo "finished"; break; fi
  if ! "${SSH[@]}" "pgrep -f 'stage_sandbox.py --out logs/sandbox/$NAME' >/dev/null"; then
    sleep 3; rsync -a -e "ssh -o BatchMode=yes -i $KEY" "$HOST:~/$RDIR/logs/sandbox/$NAME/" "$HERE/logs/sandbox/$NAME/" 2>/dev/null || true
    echo "sandbox process ended (see logs/sandbox/$NAME.log)"; break
  fi
  sleep 5
done

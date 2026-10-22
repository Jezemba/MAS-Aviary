#!/usr/bin/env bash
# Stop the optimizer-baseline runs and their SU2 solves -- and nothing else. Processes are selected by what they
# ARE (run_baseline.py's own interpreter; SU2_CFD/prterun whose arguments point into the baselines' TMPDIR),
# read from /proc, never by a pattern a shell command line could match. Optional: --su2-servers also stops the
# baseline su2 servers (ports base+10k+1 for the given bases).
set -u
TMPB=${BASELINE_TMPDIR:-$HOME/Jessica/tmp_baselines}
groups=""
for d in /proc/[0-9]*; do
  pid=${d#/proc/}
  [ -r "$d/cmdline" ] || continue
  cmd=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null) || continue
  exe=$(readlink "$d/exe" 2>/dev/null) || continue
  case "$exe" in
    */python*) [[ "$cmd" == *"scripts/optimizer_baselines/run_baseline.py"* ]] || continue ;;
    */SU2_CFD|*/prterun|*/prted) [[ "$cmd" == *"$TMPB"* ]] || continue ;;
    *) continue ;;
  esac
  g=$(ps -o pgid= -p "$pid" | tr -d ' ')
  [ -n "$g" ] && groups="$groups $g"
done
groups=$(echo $groups | tr ' ' '\n' | sort -u)
for g in $groups; do kill -TERM -- "-$g" 2>/dev/null; done
echo "stopped $(echo $groups | wc -w) process groups"
if [ "${1:-}" = "--su2-servers" ]; then
  shift
  for base in "$@"; do for w in 0 1 2 3 4 5; do
    port=$((base + 10 * w + 1))
    pid=$(ss -ltnp 2>/dev/null | grep ":$port " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
    [ -n "$pid" ] && kill -TERM -- "-$(ps -o pgid= -p "$pid" | tr -d ' ')" 2>/dev/null
  done; done
  echo "stopped baseline su2 servers for bases: $*"
fi

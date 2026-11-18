#!/usr/bin/env bash
# L2 (second review, 2026-11-18): pre-launch check of ONE run machine for the final study. Read-only: it starts,
# stops, signals and deletes nothing, and calls no server. Exit 0 only when every check passes; any failure -> 1
# (usage errors -> 2). Run it on each machine after its slot is up (start_pinned_slot.sh; wait >= 60 s for the
# A16 "loaded" lines) and before launching any combination there.
#
#   AVION_DECKS_DIR=$SLOT_HOME/decks PINS="MAS-Aviary=<sha> aviary-mcp=<sha> mass-mcp=<sha> tigl-mcp=<sha> \
#     su2-mcp=<sha> pycycle-mcp=<sha>" scripts/prelaunch_check.sh --slot-home <SLOT_HOME> [--python <py>] \
#     [--run-dir <MAS-Aviary checkout the runner starts from> ...]
#
# Checks:
#  1. deck: AVION_DECKS_DIR is set (the runner must get the SAME value) and the canonical baseline deck resolves
#     through data_plane.resolve_baseline_deck there: file present, readable, md5 = baseline.engine_deck_md5.
#  2. canonical baseline values non-null: sized_mtom_kg, engine_deck_file, engine_deck_md5, engine_scale_factor
#     (numbers > 0), and data_plane.carried_engine() / trim_mass_basis() give the baseline's values (no run context).
#  3. pinned code (A16 method, ws8 final_fix/baseline/code/a16/sitecustomize.py): in $SLOT_HOME/a16.log, for each of
#     the five servers, the latest start (pid) imported its package from a checkout at the pinned commit, tree clean,
#     OK (no MISMATCH), the 60-s "loaded" line agrees, and that pid is still running. Each run dir's MAS-Aviary
#     checkout is at the MAS-Aviary pin with a clean tracked tree.
#  4. run folders (B251): each run dir has ../mass-mcp with tests/fixtures/D150_simple.xml (AVION_ROOT = parent of
#     MAS-Aviary) and a logs/ directory.
set -uo pipefail

usage() { sed -n '2,25p' "$0" >&2; exit 2; }
SLOT_HOME="" PY="" RUN_DIRS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --slot-home) SLOT_HOME=${2:-}; shift 2 ;;
        --python) PY=${2:-}; shift 2 ;;
        --run-dir) RUN_DIRS+=("${2:-}"); shift 2 ;;
        -h|--help) usage ;;
        *) echo "unknown argument: $1" >&2; usage ;;
    esac
done
MAS_SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
[ ${#RUN_DIRS[@]} -eq 0 ] && RUN_DIRS=("$MAS_SELF")
[ -n "$SLOT_HOME" ] || { echo "--slot-home is required" >&2; usage; }
[ -n "$PY" ] || PY="$(cd "$MAS_SELF/.." && pwd -P)/.venv/bin/python"

FAIL=0
ok()   { echo "  OK   $*"; }
bad()  { echo "  FAIL $*"; FAIL=1; }

declare -A PIN=()
for kv in ${PINS:-}; do PIN["${kv%%=*}"]="${kv#*=}"; done
for repo in MAS-Aviary aviary-mcp mass-mcp tigl-mcp su2-mcp pycycle-mcp; do
    [ -n "${PIN[$repo]:-}" ] || bad "PINS has no commit for $repo (PINS=\"repo=sha ...\")"
done

echo "== 1/2. baseline deck and canonical baseline values (python: $PY)"
if [ -z "${AVION_DECKS_DIR:-}" ]; then
    bad "AVION_DECKS_DIR is not set (export it to this machine's study decks folder, e.g. \$SLOT_HOME/decks, here AND for the runner)"
elif [ ! -x "$PY" ]; then
    bad "python $PY not found"
else
    for d in "${RUN_DIRS[@]}"; do
        out=$(cd "$d" && env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY PYTHONPATH="$d" OMP_NUM_THREADS=1 \
              timeout 300 nice -n 15 "$PY" -I - "$d" <<'PYEOF' 2>&1
import hashlib, math, os, sys
sys.path.insert(0, sys.argv[1])
from src.config.canonical import load_canonical
import src.tools.data_plane as dp
base = load_canonical().get("baseline") or {}
bad = []
for k in ("sized_mtom_kg", "engine_scale_factor"):
    v = base.get(k)
    if not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0):
        bad.append(f"baseline.{k} = {v!r} (must be a number > 0)")
for k in ("engine_deck_file", "engine_deck_md5"):
    if not base.get(k):
        bad.append(f"baseline.{k} is not set")
path, how = dp.resolve_baseline_deck(base)
if path is None:
    bad.append(f"deck: {how}")
else:
    try:
        md5 = hashlib.md5(open(path, "rb").read()).hexdigest()
    except OSError as exc:
        md5 = None
        bad.append(f"deck {path} not readable: {exc}")
    if md5 is not None and md5 != str(base.get("engine_deck_md5")):
        bad.append(f"deck {path} md5 {md5} != canonical {base.get('engine_deck_md5')}")
    else:
        print(f"OK deck {path} ({how}) md5 {md5}")
deck, scale, src = dp.carried_engine()
if deck != path or scale != base.get("engine_scale_factor"):
    bad.append(f"carried_engine() = ({deck!r}, {scale!r}, {src!r}), not the baseline's ({path!r}, "
               f"{base.get('engine_scale_factor')!r})")
mass, mbasis = dp.trim_mass_basis()
if mass != base.get("sized_mtom_kg"):
    bad.append(f"trim_mass_basis() = ({mass!r}, {mbasis!r}), not baseline.sized_mtom_kg {base.get('sized_mtom_kg')!r}")
else:
    print(f"OK baseline sized_mtom_kg {mass} engine_scale_factor {scale}")
for b in bad:
    print("BAD " + b)
sys.exit(1 if bad else 0)
PYEOF
        )
        rc=$?
        while IFS= read -r line; do
            case "$line" in
                OK\ *) ok "[$d] ${line#OK }" ;;
                BAD\ *) bad "[$d] ${line#BAD }" ;;
            esac
        done <<< "$out"
        if [ $rc -ne 0 ] && ! grep -q "^BAD " <<< "$out"; then
            bad "[$d] the canonical check did not run (exit $rc): $(tail -3 <<< "$out" | tr '\n' ' ')"
        fi
    done
fi

echo "== 3. pinned code imported by each server (A16 log $SLOT_HOME/a16.log)"
A16="$SLOT_HOME/a16.log"
if [ ! -s "$A16" ]; then
    bad "$A16 missing or empty (start the slot with start_pinned_slot.sh: PYTHONPATH=\$CODE/a16:..., A16_EXPECT, A16_LOG)"
else
    # entry : package checked at start : module whose 60-s "loaded" line is checked : repo (pin key). aviary_mcp_server.py
    # runs as __main__, so its working module aviary_runner is the one A16 lists as loaded.
    for spec in "tigl-mcp:tigl_mcp:tigl_mcp:tigl-mcp" "mass-mcp-server:mass_mcp:mass_mcp:mass-mcp" \
                "su2-mcp:su2_mcp:su2_mcp:su2-mcp" "pycycle-mcp:pycycle_mcp:pycycle_mcp:pycycle-mcp" \
                "aviary_mcp_server.py:aviary_mcp_server:aviary_runner:aviary-mcp"; do
        IFS=: read -r entry pkg mod repo <<< "$spec"
        pin=${PIN[$repo]:-}
        start=$(grep -F " entry=$entry $pkg: " "$A16" | tail -1)
        if [ -z "$start" ]; then bad "$entry: no A16 start line for $pkg"; continue; fi
        pid=$(sed -E 's/.* pid=([0-9]+) .*/\1/' <<< "$start")
        commit=$(sed -E 's/.* commit=([^ ]+) .*/\1/' <<< "$start")
        mism=$(grep -F "pid=$pid entry=$entry " "$A16" | grep -E "MISMATCH|REFUSING" | head -1)
        loaded=$(grep -F "pid=$pid entry=$entry loaded $mod: " "$A16" | tail -1)
        [ -n "$mism" ] && bad "$entry pid $pid: $mism"
        if ! grep -q " tree=clean " <<< "$start" || ! grep -qE " OK$" <<< "$start"; then
            bad "$entry pid $pid: $start"
        elif [ -z "$pin" ] || [ "${commit#"$pin"}" = "$commit" ]; then
            bad "$entry pid $pid imports $pkg at commit $commit, pin ${pin:-<none>}"
        else
            ok "$entry pid $pid imports $pkg at $commit (pin $pin), tree clean"
        fi
        if [ -z "$loaded" ]; then
            bad "$entry pid $pid: no 'loaded $mod' line yet (A16 writes it 60 s after the start; wait and re-run)"
        elif ! grep -qE " OK$" <<< "$loaded" || ! grep -q " commit=$commit " <<< "$loaded" \
                || ! grep -q " tree=clean " <<< "$loaded"; then
            bad "$entry pid $pid: $loaded"
        fi
        if ! ps -p "$pid" > /dev/null 2>&1; then
            bad "$entry pid $pid (the checked start) is not running: a later server would be unchecked"
        fi
    done
fi
for d in "${RUN_DIRS[@]}"; do
    head=$(git -C "$d" rev-parse HEAD 2>/dev/null || echo none)
    dirty=$(git -C "$d" status --porcelain --untracked-files=no 2>/dev/null | wc -l)
    pin=${PIN[MAS-Aviary]:-}
    if [ -z "$pin" ] || [ "${head#"$pin"}" = "$head" ]; then
        bad "run dir $d: MAS-Aviary at $head, pin ${pin:-<none>}"
    elif [ "$dirty" -ne 0 ]; then
        bad "run dir $d: MAS-Aviary tracked tree has $dirty modified file(s)"
    else
        ok "run dir $d: MAS-Aviary at $head (pin $pin), tree clean"
    fi
done

echo "== 4. run folders (B251)"
for d in "${RUN_DIRS[@]}"; do
    fixture="$d/../mass-mcp/tests/fixtures/D150_simple.xml"
    if [ -f "$fixture" ]; then ok "run dir $d: mass-mcp beside it ($(cd "$d/../mass-mcp" && pwd -P)), D150 fixture present"
    else bad "run dir $d: no $fixture (AVION_ROOT = parent of MAS-Aviary must hold mass-mcp, B251)"; fi
    if [ -d "$d/logs" ]; then ok "run dir $d: logs/ present"
    else bad "run dir $d: no logs/ directory (git does not track it; launch_sweep.sh fails without it, B251)"; fi
done

if [ $FAIL -ne 0 ]; then echo "PRELAUNCH CHECK FAILED"; exit 1; fi
echo "PRELAUNCH CHECK PASSED"
exit 0

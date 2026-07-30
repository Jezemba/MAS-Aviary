"""Statistical batch runner — runs Aviary combinations as CUMULATIVE CHAINS.

Each combination is an independent chain of N successive runs ("links"):
  - Link 0 (Run 1) of EVERY combo starts from the SAME fixed anchor design
    (generate_random_params(base_seed) — AR, AREA, SWEEP, TAPER, fuselage dims,
    SCALE_FACTOR) plus the identical canonical SLSQP/mission and per-call discipline
    controls, so every combo's Run 1 is byte-identical.
  - Link k>0 starts from link k-1's END-STATE design — the aircraft the agents left
    (read from get_results.design_parameters.aircraft_params). The design accumulates
    across the chain; we observe the trajectory from the shared start through N links.

Only the DESIGN VARIABLES carry forward. The pinned discipline controls (mission /
SLSQP inputs, mass method=flops, material=aluminum, SU2 numerics) are re-applied from
the canonical baseline on every link and never carried — if an agent changes one, the
design ledger records the deviation and the next link resets it to canonical.

Run one combo per invocation (--combinations <one>) to get one wandb trace per chain.

Features:
  - Checkpoint file (stat_progress.json) for crash recovery
  - Per-run retry (up to 3 attempts before marking failed)
  - GPU cleanup between runs
  - Prepends set_aircraft_parameters calls to the task via a hook
  - Weights & Biases (wandb) logging for real-time monitoring

Usage:
    # Full 30-repeat run
    python scripts/stat_batch_runner.py --repeats 30

    # Test: 1 repeat, 1 combination
    python scripts/stat_batch_runner.py --repeats 1 \
        --combinations aviary_sequential_staged_pipeline

    # Resume after crash
    python scripts/stat_batch_runner.py --repeats 30  # reads checkpoint automatically

    # Dry run: just print the 30 random parameter sets
    python scripts/stat_batch_runner.py --repeats 30 --dry-run
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch

try:
    import wandb

    HAS_WANDB = True
except ImportError:
    HAS_WANDB = False

try:
    import weave

    HAS_WEAVE = True
except ImportError:
    HAS_WEAVE = False


# ---------------------------------------------------------------------------
# Parameter ranges (from get_design_space)
# ---------------------------------------------------------------------------

PARAM_RANGES = {
    "Aircraft.Wing.ASPECT_RATIO": (7.0, 14.0),
    "Aircraft.Wing.AREA": (100.0, 160.0),
    "Aircraft.Wing.SWEEP": (15.0, 40.0),
    "Aircraft.Wing.TAPER_RATIO": (0.15, 0.45),
    "Aircraft.Fuselage.LENGTH": (28.0, 50.0),
    "Aircraft.Fuselage.MAX_HEIGHT": (3.0, 5.5),
    "Aircraft.Fuselage.MAX_WIDTH": (3.0, 5.5),
    "Aircraft.Engine.SCALE_FACTOR": (0.8, 1.5),
}


def generate_random_params(seed: int) -> dict[str, float]:
    """Generate a random parameter set within valid ranges.

    SPAN is derived (read-only on MCP), so we only randomize
    the 8 settable parameters.
    """
    rng = np.random.default_rng(seed)
    params = {}
    for name, (lo, hi) in PARAM_RANGES.items():
        params[name] = round(float(rng.uniform(lo, hi)), 4)
    # Derive SPAN for logging (not sent to MCP)
    ar = params["Aircraft.Wing.ASPECT_RATIO"]
    area = params["Aircraft.Wing.AREA"]
    params["_derived_span"] = round(math.sqrt(ar * area), 4)
    return params


def generate_all_param_sets(n_repeats: int, base_seed: int = 42) -> list[dict]:
    """Generate N deterministic random parameter sets."""
    return [generate_random_params(base_seed + i) for i in range(n_repeats)]


# ---------------------------------------------------------------------------
# Pre-validated parameter sets
# ---------------------------------------------------------------------------

_VALID_PARAMS_PATH = Path("config/valid_param_sets.json")


def load_valid_param_sets(path: Path = _VALID_PARAMS_PATH) -> dict:
    """Load the pre-validated parameter sets file."""
    with open(path) as f:
        return json.load(f)


def take_next_valid_params(path: Path = _VALID_PARAMS_PATH) -> dict | None:
    """Take the next available (non-TAKEN) param set and mark it TAKEN.

    Returns the param dict, or None if all are taken.
    Writes back to the JSON file atomically.
    """
    data = load_valid_param_sets(path)
    params_list = data.get("params", [])

    for i, entry in enumerate(params_list):
        if entry.get("_status") == "TAKEN":
            continue
        # Mark as taken
        params_list[i]["_status"] = "TAKEN"
        data["params"] = params_list
        # Atomic write
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        tmp.replace(path)
        # Return a clean copy (without _status/_seed metadata)
        return dict(entry)

    return None


def uses_valid_params(combo_name: str) -> bool:
    """Return True if this combo should use pre-validated params."""
    return "aviary" in combo_name


# ---------------------------------------------------------------------------
# Checkpoint management
# ---------------------------------------------------------------------------


@dataclass
class RunKey:
    repeat: int
    combo_name: str


def load_checkpoint(path: Path) -> dict:
    """Load checkpoint file. Returns {repeat_combo: result_dict}."""
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"completed": {}, "failed": {}}


def save_checkpoint(path: Path, checkpoint: dict) -> None:
    """Atomically save checkpoint."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(checkpoint, f, indent=2, default=str)
    tmp.replace(path)


def run_key(repeat: int, combo_name: str) -> str:
    return f"r{repeat:03d}_{combo_name}"


# ---------------------------------------------------------------------------
# Pre-hook: programmatic MCP calls to set starting parameters
# ---------------------------------------------------------------------------


# One sentinel tool per MCP server — if any is missing after discovery, that
# server's tools failed to register (the nondeterministic mcpadapt/streamable-http
# discovery race that surfaces as e.g. KeyError 'create_su2_session' mid-run).
_EXPECTED_SENTINELS = {
    "tigl": "open_cpacs",
    "su2": "create_su2_session",
    "mass": "estimate_mass",
    "pycycle": "create_cycle_model",
    "aviary": "create_session",
}


def _load_mcp_tools(config, max_attempts: int = 4) -> dict:
    """Load MCP tools and return a name→tool mapping.

    Verifies every MCP server registered its tools (one sentinel per server) and
    RETRIES the whole discovery if any server dropped out — a link must never run
    with a missing discipline. Fails loud after ``max_attempts`` rather than
    silently proceeding (which previously showed up as KeyError deep in a link).
    """
    from src.tools.tool_loader import load_tools_for_agent

    last_missing: list[str] = []
    for attempt in range(1, max_attempts + 1):
        tools = load_tools_for_agent([], config)  # empty list = load all
        tool_map = {t.name: t for t in tools}
        last_missing = [
            f"{srv}({sentinel})"
            for srv, sentinel in _EXPECTED_SENTINELS.items()
            if sentinel not in tool_map
        ]
        if not last_missing:
            return tool_map
        print(
            f"  [tool-discovery] attempt {attempt}/{max_attempts}: missing "
            f"{', '.join(last_missing)} — retrying",
            flush=True,
        )
    raise RuntimeError(
        "MCP tool discovery incomplete after "
        f"{max_attempts} attempts — missing servers: {', '.join(last_missing)}. "
        "Check that all 5 MCP servers are up (tigl:8500 su2:8200 mass:8700 "
        "pycycle:8400 aviary:8600) before retrying."
    )


def _extract_session_id(resp) -> str:
    """Extract session_id from a create_session response (dict or string)."""
    if isinstance(resp, dict) and resp.get("session_id"):
        return resp["session_id"]
    # Fall back to regex extraction from string responses
    import re

    m = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        str(resp),
    )
    if m:
        return m.group(0)
    raise RuntimeError(f"create_session failed — no session_id: {resp}")


def setup_session_with_params(
    tool_map: dict,
    params: dict[str, float],
) -> dict:
    """Create session with initial params and configure mission via MCP.

    Uses the MCP's `initial_parameters` argument on `create_session` so
    parameters are applied server-side in the same call — no separate
    set_aircraft_parameters needed.

    Makes direct programmatic MCP calls — no LLM involved, no hallucination
    possible. Returns a dict with session_id and the MCP responses for
    verification.

    Raises RuntimeError if any MCP call fails.
    """
    results = {}
    settable = {k: v for k, v in params.items() if not k.startswith("_")}

    # 1. Create session WITH initial parameters (MCP validates & applies them)
    create = tool_map["create_session"]
    resp = create.forward(initial_parameters=settable)
    session_id = _extract_session_id(resp)
    results["session_id"] = session_id
    results["create_session"] = resp

    # Check for warnings from the MCP (e.g. clamped values)
    if isinstance(resp, dict) and resp.get("warnings"):
        print(f"  MCP warnings: {resp['warnings']}")

    # 2. Configure mission from the canonical baseline (single source of truth —
    #    DLR-F25: 2500 nmi, 239 pax, M0.78, FL330). Previously hardcoded a leftover
    #    A320-class 1500 nmi / M0.785 / 162 pax spec that overrode the task.
    from src.config.canonical import load_canonical
    _m = load_canonical().get("mission", {})
    configure = tool_map["configure_mission"]
    resp = configure.forward(
        session_id=session_id,
        range_nmi=_m.get("range_nmi", 2500),
        num_passengers=_m.get("num_passengers", 239),
        cruise_mach=_m.get("cruise_mach", 0.78),
        cruise_altitude_ft=_m.get("cruise_altitude_ft", 33000),
        optimizer_max_iter=_m.get("optimizer_max_iter", 200),
    )
    results["configure_mission"] = resp

    # FAIL LOUD: a rejected configure_mission would otherwise leave the session on
    # (partly) default A320 mission values, silently corrupting every run. The
    # experiment REQUIRES the identical canonical DLR-F25 mission at each run's start.
    if isinstance(resp, dict) and resp.get("error"):
        raise RuntimeError(
            f"configure_mission REJECTED the canonical mission — refusing to run on a "
            f"corrupted mission. Response: {resp}"
        )

    return results


def build_task_with_session(
    base_task: str,
    session_id: str,
    params: dict[str, float] | None = None,
    prior_feedback: str | None = None,
) -> str:
    """Build task with pre-created session_id and starting parameters.

    The session is pre-created with initial_parameters applied via MCP,
    and the task text includes the session_id and params as context.
    Strong language warns agents not to create new sessions — a new
    session will lack the configured mission and parameters, causing
    simulation failures.
    """
    params_text = ""
    if params:
        settable = {k: v for k, v in params.items() if not k.startswith("_")}
        lines = [f"  {k}: {v}" for k, v in settable.items()]
        params_text = (
            "\nThe following STARTING parameters have been applied as an "
            "initial baseline — they are valid but NOT optimized:\n"
            + "\n".join(lines)
            + "\nYour job is to IMPROVE these parameters to minimize fuel burn. "
            "Call set_aircraft_parameters to adjust values (its response now "
            "includes a 'valid' boolean — validation runs inline). If valid "
            "is false, adjust and call set_aircraft_parameters again until "
            "valid:true, then run_simulation to measure fuel burn.\n"
        )

    feedback_text = ""
    if prior_feedback:
        feedback_text = (
            "\n=== FEEDBACK FROM THE PREVIOUS DESIGN ITERATION (chain link) ===\n"
            + prior_feedback
            + "\nThe STARTING parameters above are that previous design. Use this "
            "assessment to decide what to change THIS iteration — fix the FAILED "
            "constraints and reduce fuel burn; do not just repeat the same design.\n"
            "================================================================\n"
        )

    return (
        f"IMPORTANT — A session has already been created with mission configured.\n"
        f"  session_id = {session_id}\n"
        f"  Mission: 2500 nmi, 239 pax, Mach 0.78, FL330 (DLR-F25).\n"
        f"{params_text}"
        f"{feedback_text}"
        f"Use this session_id for ALL tool calls. Session setup is done.\n"
        f"WARNING: Creating a new session (calling create_session) will "
        f"produce a blank session without the mission or starting parameters, "
        f"and simulations on it WILL FAIL.\n\n"
        f"{base_task}"
    )


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

_DEFAULT_AVIARY_TASK = (
    "Design a single-aisle commercial aircraft for 1,500 nmi range, "
    "162 passengers, cruise Mach 0.785, FL350. Optimize for minimum "
    "fuel burn. Constraints: fuel_burned_kg <= 8500, gtow_kg <= 72000."
)

_DEFAULT_MDO_F25_TASK = (
    "Design a DLR-F25 class aircraft for minimum fuel burn. "
    "Use CPACS file at /home/aipexws3/Jessica/Avion/mass-mcp/tests/fixtures/D150_simple.xml. "
    "Target: 2500 nmi range, 239 passengers, Mach 0.78 cruise, 33000 ft altitude. "
    "Constraints: fuel_burned_kg <= 15000, gtow_kg <= 90000. "
    "Report the optimality gap versus the F25 reference (MTOM 85700 kg, fuel 12100 kg)."
)

_DEFAULT_TIMEOUT_MINUTES = 20


# ---------------------------------------------------------------------------
# Timeout wrapper — runs run_combination with a wall-clock deadline
# ---------------------------------------------------------------------------


def _aggressive_gpu_cleanup():
    """Force-free all GPU memory between runs.

    Deletes all TransformersModel / smolagents agent references from
    every module's namespace, then runs gc + empty_cache.  This prevents
    CUDA OOM when daemon threads from timed-out runs hold stale model refs.
    """
    import sys

    # 1. Walk loaded modules and delete heavy objects
    heavy_types = ("TransformersModel", "ToolCallingAgent", "MultiStepAgent")
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        for attr_name in list(getattr(mod, "__dict__", {}).keys()):
            try:
                obj = getattr(mod, attr_name, None)
                if obj is not None and type(obj).__name__ in heavy_types:
                    delattr(mod, attr_name)
            except Exception:
                pass

    # 2. Garbage collect
    gc.collect()
    gc.collect()

    # 3. Clear CUDA cache on all devices
    if torch.cuda.is_available():
        for i in range(torch.cuda.device_count()):
            with torch.cuda.device(i):
                torch.cuda.empty_cache()

    mem = torch.cuda.memory_allocated() / 1024**2 if torch.cuda.is_available() else 0
    print(f"  [GPU cleanup] {mem:.0f} MB allocated after cleanup")


def _run_with_timeout(combo, task, config, domain, timeout_seconds, session_id=None):
    """Run a combination with a wall-clock timeout.

    Uses a subprocess so that on timeout the entire process (and its GPU
    memory) can be killed cleanly — no leaked daemon threads.
    """
    import threading

    result_box = [None]
    error_box = [None]

    def _target():
        try:
            from src.runners.batch_runner import run_combination

            result_box[0] = run_combination(
                combo,
                task,
                config,
                session_id=session_id,
            )
        except Exception as e:
            error_box[0] = e

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=timeout_seconds)

    if t.is_alive():
        # Force GPU cleanup even though the thread is still alive
        _aggressive_gpu_cleanup()
        raise TimeoutError(f"Run exceeded {timeout_seconds / 60:.0f}m timeout")
    if error_box[0] is not None:
        raise error_box[0]
    return result_box[0]


def _is_zero_fuel(result) -> bool:
    """Check if a result has zero fuel (simulation didn't produce output)."""
    ec = getattr(result, "eval_classification", None) or {}
    fuel = ec.get("fuel_burned_kg", None)
    return fuel is not None and fuel == 0.0


def read_end_state_design(tool_map: dict, session_id: str) -> dict:
    """Read the aircraft design the agents LEFT at the end of a run, to seed the next
    repeat in this combo's cumulative chain.

    Authoritative: pulls the session's current aircraft_params from get_results
    (nested under design_parameters.aircraft_params), filtered to the 8 settable
    design variables (SPAN is derived/read-only, excluded). Returns {} on any
    failure — the caller then keeps the previous chain state so the chain never
    silently resets to the anchor mid-run. Only the design variables carry forward;
    the pinned discipline controls (mission/SLSQP, mass method, material, SU2) are
    re-applied from canonical on every run and never carried."""
    try:
        resp = tool_map["get_results"].forward(session_id=session_id)
        d = resp if isinstance(resp, dict) else json.loads(resp)
        ap = (d.get("design_parameters") or {}).get("aircraft_params") or {}
        return {k: v for k, v in ap.items() if k in PARAM_RANGES}
    except Exception as e:
        print(f"  [chain] end-state read failed (keeping prior design): {e}")
        return {}


def run_stat_batch(
    n_repeats: int,
    combo_names: list[str] | None = None,
    config_path: str = "config/aviary_run.yaml",
    output_dir: str | None = None,
    max_retries: int = 3,
    base_seed: int = 42,
    dry_run: bool = False,
    timeout_minutes: float = _DEFAULT_TIMEOUT_MINUTES,
) -> None:
    """Run statistical batch: all combos × N repeats with checkpointing."""

    from src.config.loader import load_config
    from src.runners.batch_runner import (
        AVIARY_COMBINATIONS,
    )

    # Setup
    config = load_config(config_path)
    all_combos = AVIARY_COMBINATIONS

    # Filter to non-placeholder combos.
    _SKIP = {"placeholder"}
    combos = [c for c in all_combos if "placeholder" not in c.name and c.name not in _SKIP]
    if combo_names:
        combos = [c for c in combos if c.name in combo_names]

    if not combos:
        print("No matching combinations found.")
        return

    # The chain's fixed anchor: link 0 of every combo starts here (see run loop).
    anchor_preview = {k: v for k, v in generate_random_params(base_seed).items()
                      if not k.startswith("_")}

    if output_dir is None:
        output_dir = f"logs/stat_results/{int(time.time())}"
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Save the anchor for reproducibility. Only link 0 uses it directly; links 1..N-1
    # start from the previous link's end-state (recorded per-run in chain_start_params).
    params_file = out_path / "chain_anchor.json"
    with open(params_file, "w") as f:
        json.dump(
            {"base_seed": base_seed, "n_links": n_repeats, "mode": "cumulative_chain",
             "anchor_params": anchor_preview},
            f,
            indent=2,
        )

    if dry_run:
        print(f"CUMULATIVE CHAIN mode: each combo = {n_repeats} links.")
        print(f"Link 0 anchor (identical for every combo, seed={base_seed}):")
        for k, v in anchor_preview.items():
            print(f"    {k}: {v}")
        print(f"Links 1..{n_repeats-1} start from the previous link's end-state design.")
        print(f"\nWould run {len(combos)} combos × {n_repeats} links = {len(combos) * n_repeats} runs")
        print(f"Combinations: {[c.name for c in combos]}")
        return

    # Load or create checkpoint
    ckpt_path = out_path / "stat_progress.json"
    checkpoint = load_checkpoint(ckpt_path)

    total_runs = len(combos) * n_repeats
    completed = len(checkpoint["completed"])
    failed = len(checkpoint["failed"])
    print(f"Statistical batch: {len(combos)} combos × {n_repeats} repeats = {total_runs} runs")
    print(f"Checkpoint: {completed} completed, {failed} failed, {total_runs - completed - failed} remaining")
    print(f"Output: {out_path}/")
    print()

    # Initialize wandb + weave. Project name comes from $WANDB_PROJECT so a
    # batch can be directed into a separate project without source edits;
    # falls back to the long-standing default for existing workflows.
    wb_project = os.environ.get("WANDB_PROJECT", "mas-aviary-stat")
    wb_run = None
    if HAS_WANDB:
        # One chain per combo => name the trace after the combo so the 8 paper
        # chains are cleanly labeled. Multi-combo invocations keep the generic name.
        if len(combos) == 1:
            _tag = combos[0].name.replace("mdo_f25_", "")
            _wb_name = f"chain{n_repeats}_{_tag}_{int(time.time())}"
        else:
            _wb_name = f"stat_{n_repeats}x{len(combos)}_{int(time.time())}"
        wb_run = wandb.init(
            project=wb_project,
            name=_wb_name,
            config={
                "n_repeats": n_repeats,
                "n_combos": len(combos),
                "base_seed": base_seed,
                "max_retries": max_retries,
                "combo_names": [c.name for c in combos],
            },
            resume="allow",
        )
    # Weave auto-traces all smolagents agent.run() calls
    if HAS_WEAVE:
        weave.init(project_name=wb_project)

    # Load MCP tools once for programmatic pre-hook calls
    print("Loading MCP tools for session setup...")
    tool_map = _load_mcp_tools(config)
    print(f"  Loaded {len(tool_map)} tools: {sorted(tool_map.keys())}")

    # Fixed anchor: repeat 0 of EVERY combo starts from this identical, pre-validated
    # design. Combined with the canonical SLSQP/mission (configure_mission) and the
    # per-call canonical discipline controls, Run 1 is byte-identical across all combos.
    anchor_params = {k: v for k, v in generate_random_params(base_seed).items()
                     if not k.startswith("_")}

    run_count = 0
    for combo in combos:
        print(f"\n{'=' * 60}")
        print(f"COMBO: {combo.name} — CUMULATIVE CHAIN ({n_repeats} links)")
        print(f"{'=' * 60}")

        # Reset the chain to the anchor at the START of each combo. Within a combo,
        # repeat k>0 starts from repeat k-1's END-STATE design (carry-forward below),
        # so each combo is an independent trajectory from the shared anchor.
        chain_params = dict(anchor_params)
        chain_feedback: str | None = None  # prior link's assessment, fed to the next link
        # Resume support: if earlier links of this combo already completed, advance the
        # chain to the last completed link's end-state so a resumed run continues.
        for prev in range(n_repeats):
            done = checkpoint["completed"].get(run_key(prev, combo.name))
            if done and done.get("chain_end_params"):
                chain_params = dict(done["chain_end_params"])
                chain_feedback = done.get("chain_feedback") or chain_feedback

        for repeat_idx in range(n_repeats):
            key = run_key(repeat_idx, combo.name)

            # Skip if already done
            if key in checkpoint["completed"] or key in checkpoint["failed"]:
                continue

            # Chain link: repeat 0 = anchor; repeat k>0 = prior link's end-state design.
            # The pre-validated-pool override is intentionally NOT used — the chain
            # supplies deterministic starting params so every combo's Run 1 is identical.
            params = dict(chain_params)

            run_count += 1
            # GPU cleanup between runs
            if run_count > 1:
                try:
                    _aggressive_gpu_cleanup()
                except Exception:
                    pass

            print(f"[{run_count}/{total_runs - completed - failed}] repeat={repeat_idx:03d} "
                  f"combo={combo.name} (chain link {repeat_idx + 1}/{n_repeats})")

            # Pre-hook: create session and set starting params via MCP
            try:
                setup = setup_session_with_params(tool_map, params)
                session_id = setup["session_id"]
                print(f"  Session {session_id[:8]}... params set via MCP")
            except Exception as e:
                print(f"  PRE-HOOK FAILED: {e}")
                checkpoint["failed"][key] = {
                    "repeat_index": repeat_idx,
                    "combo_name": combo.name,
                    "error": f"pre-hook: {e}",
                    "attempts": 0,
                    "initial_params": {k: v for k, v in params.items() if not k.startswith("_")},
                    "seed": base_seed + repeat_idx,
                }
                save_checkpoint(ckpt_path, checkpoint)
                if wb_run:
                    wandb.log(
                        {
                            "repeat": repeat_idx,
                            "combo": combo.name,
                            "org_structure": combo.org_structure,
                            "handler": combo.handler,
                            "status": "failed",
                            "error": f"pre-hook: {e}",
                            "completed_total": len(checkpoint["completed"]),
                            "failed_total": len(checkpoint["failed"]),
                        }
                    )
                continue

            base_task = _DEFAULT_MDO_F25_TASK if combo.name.startswith("mdo_f25_") else _DEFAULT_AVIARY_TASK
            task = build_task_with_session(base_task, session_id, params, prior_feedback=chain_feedback)

            # Retry loop
            timeout_sec = timeout_minutes * 60
            last_error = None
            for attempt in range(1, max_retries + 1):
                try:
                    result = _run_with_timeout(
                        combo,
                        task,
                        config,
                        domain="aviary",
                        timeout_seconds=timeout_sec,
                        session_id=session_id,
                    )

                    # Retry on zero fuel (simulation didn't produce output)
                    if _is_zero_fuel(result):
                        last_error = "zero fuel_burned_kg — simulation produced no output"
                        print(f"  attempt {attempt}/{max_retries}: zero fuel, retrying...")
                        if attempt < max_retries:
                            try:
                                _aggressive_gpu_cleanup()
                            except Exception:
                                pass
                            # Chain: retry from the SAME deterministic start — do NOT
                            # regenerate params. The chain requires a fixed start per
                            # link; the agent's own stochasticity gives the retry a
                            # fresh trajectory from the identical starting design.
                            try:
                                setup = setup_session_with_params(tool_map, params)
                                session_id = setup["session_id"]
                                base_task = _DEFAULT_MDO_F25_TASK if combo.name.startswith("mdo_f25_") else _DEFAULT_AVIARY_TASK
                                task = build_task_with_session(base_task, session_id, params, prior_feedback=chain_feedback)
                            except Exception as e:
                                last_error = f"pre-hook retry: {e}"
                                print(f"  pre-hook retry failed: {e}")
                        continue

                    # Save result
                    result_dict = _safe_result_dict(result)
                    result_dict["repeat_index"] = repeat_idx
                    # Anchor seed is the chain's Run-1 seed (identical for every combo).
                    result_dict["seed"] = base_seed
                    _start = {k: v for k, v in params.items() if not k.startswith("_")}
                    result_dict["initial_params"] = _start
                    result_dict["session_id"] = session_id
                    result_dict["attempt"] = attempt

                    # --- Cumulative chain bookkeeping ---
                    # chain_start_params: the design this link STARTED from (anchor for
                    #   link 0, prior link's end-state after).
                    # chain_end_params: the design the agents LEFT — seeds the NEXT link.
                    result_dict["chain_link"] = repeat_idx
                    result_dict["chain_start_params"] = _start
                    end_state = read_end_state_design(tool_map, session_id)
                    result_dict["chain_end_params"] = end_state
                    if end_state:
                        chain_params = dict(end_state)
                        moved = sum(1 for k in end_state if abs(float(end_state[k]) - float(_start.get(k, end_state[k]))) > 1e-9)
                        print(f"  [chain] captured end-state ({moved}/{len(end_state)} vars changed) → seeds next link")
                    else:
                        print("  [chain] no end-state captured — next link reuses this link's start")

                    # --- Cross-link FEEDBACK: pass this link's FULL MDO-integrator
                    # assessment (DESIGN_STATE: constraint verdicts, DISCIPLINARY_CONFLICTS,
                    # RECOMMENDED_CHANGE, MDO_REASONING) into the NEXT link's task, so the
                    # chain gets the actionable guidance the integrator produced — not just
                    # the parsed numbers. Falls back to the parsed eval if no text found. ---
                    _ec = result.eval_classification or {}

                    def _pf(v):
                        try:
                            return f"{float(v):.1f}"
                        except (TypeError, ValueError):
                            return str(v)

                    _num = (
                        f"  fuel_burned_kg = {_pf(_ec.get('fuel_burned_kg'))} "
                        f"(constraint <=15000: {'PASS' if _ec.get('fuel_pass') else 'FAIL'})\n"
                        f"  gtow_kg = {_pf(_ec.get('gtow_kg'))} "
                        f"(constraint <=90000: {'PASS' if _ec.get('gtow_pass') else 'FAIL'})\n"
                        f"  wing_mass_kg = {_pf(_ec.get('wing_mass_kg'))} "
                        f"({'PASS' if _ec.get('wing_mass_pass') else 'FAIL'})"
                    ) if _ec else ""

                    # Pull the integrator's full final text from the last relevant message.
                    _integrator = ""
                    try:
                        for _m in reversed(result.messages or []):
                            _c = (_m.get("content") if isinstance(_m, dict)
                                  else getattr(_m, "content", "")) or ""
                            if ("DESIGN_STATE" in _c or "RECOMMENDED_CHANGE" in _c
                                    or "VERDICT" in _c or "MDO_REASONING" in _c):
                                _integrator = _c
                                break
                        if not _integrator and result.messages:
                            _last = result.messages[-1]
                            _integrator = (_last.get("content") if isinstance(_last, dict)
                                           else getattr(_last, "content", "")) or ""
                    except Exception:
                        _integrator = ""
                    if len(_integrator) > 7000:  # cap to keep the task prompt bounded
                        _integrator = _integrator[:7000] + "\n...[assessment truncated]"

                    if _integrator or _num:
                        chain_feedback = (
                            f"Previous iteration (link {repeat_idx + 1}) — quick metrics:\n"
                            f"{_num}\n\n"
                            f"Full MDO-integrator assessment of that design "
                            f"(constraint verdicts, disciplinary conflicts, and the "
                            f"integrator's RECOMMENDED_CHANGE — ACT ON THESE):\n"
                            f"{_integrator}"
                        )
                        result_dict["chain_feedback"] = chain_feedback

                    checkpoint["completed"][key] = result_dict
                    save_checkpoint(ckpt_path, checkpoint)

                    # Save per-run trace
                    run_dir = out_path / f"repeat_{repeat_idx:03d}" / combo.name
                    run_dir.mkdir(parents=True, exist_ok=True)
                    with open(run_dir / "result.json", "w") as f:
                        json.dump(result_dict, f, indent=2, default=str)
                    if result.traces:
                        with open(run_dir / "trace.json", "w") as f:
                            json.dump(result.traces, f, indent=2, default=str)

                    # Design ledger: one cross-run row {design applied -> MCP
                    # outcomes -> objective -> cost}. Passive; never affects a run.
                    from src.logging.design_ledger import append_record
                    append_record(result_dict, result.traces or {})

                    # Print summary
                    ec = result.eval_classification or {}
                    fuel = ec.get("fuel_burned_kg", "?")
                    eval_res = ec.get("result", "?")
                    print(
                        f"  → {result.status} | eval={eval_res} | fuel={fuel} | "
                        f"{result.total_turns}t | {result.duration_seconds:.0f}s "
                        f"(attempt {attempt})"
                    )

                    # Log to wandb
                    if wb_run:
                        log_data = {
                            "repeat": repeat_idx,
                            "combo": combo.name,
                            "org_structure": combo.org_structure,
                            "handler": combo.handler,
                            "status": result.status,
                            "eval_result": ec.get("result"),
                            "fuel_burned_kg": ec.get("fuel_burned_kg"),
                            "gtow_kg": ec.get("gtow_kg"),
                            "wing_mass_kg": ec.get("wing_mass_kg"),
                            "optimality_gap_pct": ec.get("optimality_gap_pct"),
                            "converged": ec.get("converged"),
                            "duration_seconds": result.duration_seconds,
                            "total_turns": result.total_turns,
                            "attempt": attempt,
                            "completed_total": len(checkpoint["completed"]),
                            "failed_total": len(checkpoint["failed"]),
                        }
                        # Add initial params
                        for k, v in params.items():
                            if not k.startswith("_"):
                                short = k.split(".")[-1]
                                log_data[f"init_{short}"] = v
                        wandb.log(log_data)

                    last_error = None
                    break

                except Exception as e:
                    last_error = f"{type(e).__name__}: {e}"
                    print(f"  attempt {attempt}/{max_retries} failed: {last_error}")
                    if attempt < max_retries:
                        try:
                            _aggressive_gpu_cleanup()
                        except Exception:
                            pass
                        # Chain: retry from the SAME deterministic start (do NOT
                        # regenerate params — a chain link must keep its fixed start).
                        try:
                            setup = setup_session_with_params(tool_map, params)
                            session_id = setup["session_id"]
                            base_task = _DEFAULT_MDO_F25_TASK if combo.name.startswith("mdo_f25_") else _DEFAULT_AVIARY_TASK
                            task = build_task_with_session(base_task, session_id, params, prior_feedback=chain_feedback)
                        except Exception as re_e:
                            print(f"  pre-hook retry failed: {re_e}")

            if last_error:
                checkpoint["failed"][key] = {
                    "repeat_index": repeat_idx,
                    "combo_name": combo.name,
                    "error": last_error,
                    "attempts": max_retries,
                }
                save_checkpoint(ckpt_path, checkpoint)
                print(f"  FAILED after {max_retries} attempts: {last_error}")
                if wb_run:
                    wandb.log(
                        {
                            "repeat": repeat_idx,
                            "combo": combo.name,
                            "org_structure": combo.org_structure,
                            "handler": combo.handler,
                            "status": "failed",
                            "error": last_error,
                            "attempt": max_retries,
                            "completed_total": len(checkpoint["completed"]),
                            "failed_total": len(checkpoint["failed"]),
                        }
                    )

    # Final summary
    completed = len(checkpoint["completed"])
    failed = len(checkpoint["failed"])
    print(f"\n{'=' * 60}")
    print(f"DONE: {completed} completed, {failed} failed out of {total_runs}")
    print(f"Results: {out_path}/")

    # Save aggregate summary
    _save_aggregate_summary(out_path, checkpoint, combos, n_repeats)

    # Finish wandb run
    if wb_run:
        # Log final summary table
        if checkpoint["completed"]:
            rows = []
            for key, res in checkpoint["completed"].items():
                ec = res.get("eval_classification") or {}
                rows.append(
                    [
                        res.get("repeat_index"),
                        res.get("name"),
                        res.get("org_structure"),
                        res.get("handler"),
                        res.get("status"),
                        ec.get("result"),
                        ec.get("fuel_burned_kg"),
                        ec.get("gtow_kg"),
                        ec.get("optimality_gap_pct"),
                        res.get("duration_seconds"),
                        res.get("total_turns"),
                    ]
                )
            table = wandb.Table(
                columns=[
                    "repeat",
                    "combo",
                    "org",
                    "handler",
                    "status",
                    "eval",
                    "fuel_kg",
                    "gtow_kg",
                    "gap_pct",
                    "duration_s",
                    "turns",
                ],
                data=rows,
            )
            wandb.log({"results_table": table})
        wandb.finish()


def _safe_result_dict(result) -> dict:
    """Convert CombinationResult to a JSON-safe dict (no traces)."""
    d = {}
    for field_name in [
        "name",
        "org_structure",
        "handler",
        "status",
        "error_message",
        "duration_seconds",
        "total_turns",
        "total_tokens",
        "token_breakdown",
        "cost_usd",
        "gpu_memory_mb",
        "eval_classification",
        "cross_strategy_metrics",
        "org_theory_metrics",
    ]:
        d[field_name] = getattr(result, field_name, None)
    # Include messages (without traces)
    d["messages"] = getattr(result, "messages", [])
    return d


def _save_aggregate_summary(
    out_path: Path,
    checkpoint: dict,
    combos: list,
    n_repeats: int,
) -> None:
    """Save a high-level summary CSV and JSON for analysis."""
    rows = []
    for key, result in checkpoint["completed"].items():
        ec = result.get("eval_classification") or {}
        rows.append(
            {
                "repeat": result.get("repeat_index"),
                "combo": result.get("name"),
                "org_structure": result.get("org_structure"),
                "handler": result.get("handler"),
                "status": result.get("status"),
                "eval_result": ec.get("result"),
                "fuel_burned_kg": ec.get("fuel_burned_kg"),
                "gtow_kg": ec.get("gtow_kg"),
                "wing_mass_kg": ec.get("wing_mass_kg"),
                "optimality_gap_pct": ec.get("optimality_gap_pct"),
                "converged": ec.get("converged"),
                "duration_seconds": result.get("duration_seconds"),
                "total_turns": result.get("total_turns"),
                "total_tokens": result.get("total_tokens"),
                "cost_usd": result.get("cost_usd"),
                "attempt": result.get("attempt"),
                "seed": result.get("seed"),
            }
        )

    # Save as JSON
    summary = {
        "n_repeats": n_repeats,
        "n_combos": len(combos),
        "total_runs": n_repeats * len(combos),
        "completed": len(checkpoint["completed"]),
        "failed": len(checkpoint["failed"]),
        "combo_names": [c.name for c in combos],
        "results": rows,
        "failures": checkpoint["failed"],
    }
    with open(out_path / "stat_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # Save as CSV for easy analysis
    if rows:
        import csv

        csv_path = out_path / "stat_summary.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"Summary CSV: {csv_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Statistical batch runner for Aviary (N repeats × 8 combos)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=30,
        help="Number of repeats per combination (default: 30)",
    )
    parser.add_argument(
        "--combinations",
        type=str,
        nargs="*",
        default=None,
        help="Specific combination names (default: all 8 non-placeholder)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/aviary_run.yaml",
        help="Path to AppConfig YAML",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory (default: logs/stat_results/{timestamp})",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retry attempts per run (default: 3)",
    )
    parser.add_argument(
        "--base-seed",
        type=int,
        default=42,
        help="Base random seed (default: 42)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print parameter sets without running",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_TIMEOUT_MINUTES,
        help=f"Per-run timeout in minutes (default: {_DEFAULT_TIMEOUT_MINUTES})",
    )
    parser.add_argument(
        "--mcp-url",
        type=str,
        default=None,
        help="MCP server URL (overrides config; e.g. http://127.0.0.1:8600/mcp)",
    )
    args = parser.parse_args()

    # Allow --mcp-url to override via environment variable.
    if args.mcp_url:
        os.environ["MAS_AVIARY_MCP_URL"] = args.mcp_url

    run_stat_batch(
        n_repeats=args.repeats,
        combo_names=args.combinations,
        config_path=args.config,
        output_dir=args.output_dir,
        max_retries=args.max_retries,
        base_seed=args.base_seed,
        dry_run=args.dry_run,
        timeout_minutes=args.timeout,
    )


if __name__ == "__main__":
    main()

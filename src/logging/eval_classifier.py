"""Eval result classifier for Aviary batch runs.

Given eval results and threshold config, classifies each run as one of:
  - success: all metrics within thresholds
  - omission: system produced output, no agent flagged issues, but
              eval metrics outside thresholds
  - commission: an agent reviewed/approved output during execution,
                but eval shows metrics outside thresholds
  - sim_fail: the mission did not fly (no Aviary result, or no fuel burned)
A flown mission whose optimiser did not converge is classified on its metrics like any other,
with converged=False and a warning in the reason (B171): whether that matters is the agents' call.
  - eval_skipped: eval was not performed
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Aviary evaluation
# ---------------------------------------------------------------------------

_AVIARY_METRIC_NAMES = [
    "fuel_burned_kg",
    "gtow_kg",
    "wing_mass_kg",
    "reserve_fuel_kg",
    "zero_fuel_weight_kg",
]

# A flown mission burns fuel; at or below this it did not fly (same floor as the data plane's
# _ZERO_FUEL_FLOOR_KG and stat_batch_runner.ZERO_FUEL_FLOOR_KG).
ZERO_FUEL_FLOOR_KG = 100.0

# Aviary-specific approval/issue keywords (from mdo_integrator VERDICT output).
_AVIARY_APPROVAL_KEYWORDS = {"COMPLETE", "TASK_COMPLETE"}
_AVIARY_ISSUE_KEYWORDS = {"RETRY", "CONTINUE", "FAILED"}


@dataclass
class AviaryEvalThresholds:
    """Aviary evaluation thresholds.

    ``limits`` (V12): upper limits the task states (fuel <= 15,000 kg, GTOW <= 90,000 kg). When set, a metric with a
    limit passes when it is at or below it; metrics without a limit are recorded, not scored; ``reference`` is the
    final study's coupled baseline, reported as a signed difference. Without limits (an old thresholds file) the
    legacy +/- max_deviation_pct band around ``reference`` applies.
    """

    reference: dict[str, float]
    max_deviation_pct: float
    converged_required: bool
    limits: dict[str, float] | None = None


def load_aviary_thresholds(
    path: str | Path = "config/eval_thresholds.yaml",
) -> AviaryEvalThresholds:
    """Load Aviary thresholds from YAML file."""
    with open(path, "r") as f:
        raw = yaml.safe_load(f)

    aviary = raw.get("aviary", {})
    reference = aviary.get("reference", {})
    return AviaryEvalThresholds(
        reference={k: float(v) for k, v in reference.items()},
        max_deviation_pct=float(aviary.get("max_deviation_pct", 10.0)),
        converged_required=bool(aviary.get("converged_required", True)),
    )


def study_thresholds() -> AviaryEvalThresholds:
    """V12: the task's requirements and the final study's coupled baseline (canonical ``evaluation``).

    Replaces the 2026-07-28 SLSQP reference (fuel 12,612.80 / GTOW 73,705.42 / wing 7,975.44 / reserve 1,360.78 /
    ZFW 59,731.84 kg, scored as a +/-10 % band), which made every link of the final study omission/commission. Raises
    when the canonical file lacks the section: there is no fallback reference.
    """
    from src.config.canonical import load_canonical

    ev = load_canonical().get("evaluation") or {}
    limits = {k: float(v) for k, v in (ev.get("requirements") or {}).items()}
    reference = {k: float(v) for k, v in (ev.get("baseline_reference") or {}).items()}
    if not limits or not reference:
        raise ValueError("canonical evaluation.requirements / baseline_reference missing: no evaluation reference")
    return AviaryEvalThresholds(reference=reference, max_deviation_pct=10.0, converged_required=True, limits=limits)


DEFAULT_AVIARY_THRESHOLDS = study_thresholds()


@dataclass
class AviaryEvalClassification:
    """Result of classifying an Aviary eval run."""

    result: str  # "success" | "omission" | "commission" | "sim_fail" | "eval_skipped"
    converged: bool
    fuel_burned_kg: float
    gtow_kg: float
    wing_mass_kg: float
    reserve_fuel_kg: float
    zero_fuel_weight_kg: float
    optimality_gap_pct: float
    fuel_pass: bool
    gtow_pass: bool
    wing_mass_pass: bool
    reserve_fuel_pass: bool
    zero_fuel_weight_pass: bool
    reason: str = ""


def classify_aviary_eval(
    eval_result: dict | None,
    *,
    converged: bool = True,
    agent_approved: bool = False,
    agent_flagged_issues: bool = False,
    thresholds: AviaryEvalThresholds | None = None,
) -> AviaryEvalClassification:
    """Classify an Aviary eval run result.

    Args:
        eval_result: Dict with keys: fuel_burned_kg, gtow_kg, wing_mass_kg,
            reserve_fuel_kg, zero_fuel_weight_kg, optimality_gap_pct.
            None if eval was skipped.
        converged: Whether the simulation converged.
        agent_approved: Whether mdo_integrator gave VERDICT: COMPLETE.
        agent_flagged_issues: Whether mdo_integrator gave VERDICT: RETRY/CONTINUE.
        thresholds: Aviary thresholds. Defaults to DEFAULT_AVIARY_THRESHOLDS.

    Returns:
        AviaryEvalClassification with result and metric details.
    """
    t = thresholds or DEFAULT_AVIARY_THRESHOLDS

    # Eval skipped.
    if eval_result is None:
        return AviaryEvalClassification(
            result="eval_skipped",
            converged=converged,
            fuel_burned_kg=0.0,
            gtow_kg=0.0,
            wing_mass_kg=0.0,
            reserve_fuel_kg=0.0,
            zero_fuel_weight_kg=0.0,
            optimality_gap_pct=0.0,
            fuel_pass=False,
            gtow_pass=False,
            wing_mass_pass=False,
            reserve_fuel_pass=False,
            zero_fuel_weight_pass=False,
            reason="Eval data not extracted from agent messages",
        )

    # The mission did not fly (B171: non-convergence alone is a warning, not a failure).
    try:
        _fuel = float(eval_result.get("fuel_burned_kg"))
    except (TypeError, ValueError):
        _fuel = None
    # Fuel not extracted: fall back to aviary's own flag, as before (missing metrics are skipped).
    flew = _fuel > ZERO_FUEL_FLOOR_KG if _fuel is not None else converged
    if not flew:
        return AviaryEvalClassification(
            result="sim_fail",
            converged=converged,
            fuel_burned_kg=eval_result.get("fuel_burned_kg") or 0.0,
            gtow_kg=eval_result.get("gtow_kg") or 0.0,
            wing_mass_kg=eval_result.get("wing_mass_kg") or 0.0,
            reserve_fuel_kg=eval_result.get("reserve_fuel_kg") or 0.0,
            zero_fuel_weight_kg=eval_result.get("zero_fuel_weight_kg") or 0.0,
            optimality_gap_pct=eval_result.get("optimality_gap_pct") or 0.0,
            fuel_pass=False,
            gtow_pass=False,
            wing_mass_pass=False,
            reserve_fuel_pass=False,
            zero_fuel_weight_pass=False,
            reason=("The mission did not fly: " + (f"{_fuel:.1f} kg of fuel burned" if _fuel is not None
                                                  else "no fuel result and Aviary did not converge")),
        )

    # Extract metrics and check them. None means "metric not extracted".
    def _check(name: str) -> tuple[float, bool, bool]:
        """Returns (actual_value, passed, was_found)."""
        raw = eval_result.get(name)
        if t.limits is not None:
            # V12: the task's requirements. A required metric that was not measured does not pass (no silent
            # default); a metric without a requirement is recorded, not scored.
            limit = t.limits.get(name)
            if raw is None:
                return 0.0, limit is None, False
            actual = float(raw)
            return actual, (limit is None or actual <= limit), True
        if raw is None:
            return 0.0, True, False  # Missing → skip (don't penalise)
        actual = float(raw)
        ref = t.reference.get(name, 0.0)
        if ref == 0.0:
            return actual, True, True
        deviation = abs(actual - ref) / ref * 100.0
        return actual, deviation <= t.max_deviation_pct, True

    fuel, fuel_pass, fuel_found = _check("fuel_burned_kg")
    gtow, gtow_pass, gtow_found = _check("gtow_kg")
    wing, wing_pass, wing_found = _check("wing_mass_kg")
    reserve, reserve_pass, reserve_found = _check("reserve_fuel_kg")
    zfw, zfw_pass, zfw_found = _check("zero_fuel_weight_kg")
    gap = float(eval_result.get("optimality_gap_pct", 0.0))

    _warning = "" if converged else " (warning: Aviary's optimiser did not converge; the mission flew)"

    # Only count found metrics for pass/fail — missing metrics are skipped.
    all_pass = fuel_pass and gtow_pass and wing_pass and reserve_pass and zfw_pass

    if all_pass:
        return AviaryEvalClassification(
            result="success",
            converged=converged,
            fuel_burned_kg=fuel,
            gtow_kg=gtow,
            wing_mass_kg=wing,
            reserve_fuel_kg=reserve,
            zero_fuel_weight_kg=zfw,
            optimality_gap_pct=gap,
            fuel_pass=True,
            gtow_pass=True,
            wing_mass_pass=True,
            reserve_fuel_pass=True,
            zero_fuel_weight_pass=True,
            reason=("All task requirements met" if t.limits is not None else "All metrics within thresholds") + _warning,
        )

    # Failed — determine omission vs commission.
    failed = []
    for name, actual, passed, found in [
        ("fuel_burned_kg", fuel, fuel_pass, fuel_found),
        ("gtow_kg", gtow, gtow_pass, gtow_found),
        ("wing_mass_kg", wing, wing_pass, wing_found),
        ("reserve_fuel_kg", reserve, reserve_pass, reserve_found),
        ("zero_fuel_weight_kg", zfw, zfw_pass, zfw_found),
    ]:
        if passed:
            continue
        ref = t.reference.get(name, 0.0)
        if t.limits is not None:
            limit = t.limits.get(name)
            if not found:
                failed.append(f"{name} not measured (requirement <= {limit:g})")
                continue
            vs = f"; {(actual - ref) / ref * 100.0:+.2f}% vs the coupled baseline {ref:.1f}" if ref else ""
            failed.append(f"{name} {actual:.1f} > requirement {limit:g}{vs}")
        elif found:
            dev = abs(actual - ref) / ref * 100.0 if ref else 0.0
            failed.append(f"{name} {actual:.1f} ({dev:.1f}% from ref {ref:.1f})")
    detail = "; ".join(failed)

    result_type = "commission" if agent_approved and not agent_flagged_issues else "omission"
    reason = (
        f"Agent approved but metrics failed: {detail}"
        if result_type == "commission"
        else f"Metrics outside thresholds: {detail}"
    )

    return AviaryEvalClassification(
        result=result_type,
        converged=converged,
        fuel_burned_kg=fuel,
        gtow_kg=gtow,
        wing_mass_kg=wing,
        reserve_fuel_kg=reserve,
        zero_fuel_weight_kg=zfw,
        optimality_gap_pct=gap,
        fuel_pass=fuel_pass,
        gtow_pass=gtow_pass,
        wing_mass_pass=wing_pass,
        reserve_fuel_pass=reserve_pass,
        zero_fuel_weight_pass=zfw_pass,
        reason=reason + _warning,
    )


def detect_aviary_agent_signals(
    agent_outputs: list[str],
) -> tuple[bool, bool]:
    """Detect Aviary-specific approval/issue signals from mdo_integrator.

    Returns:
        (agent_approved, agent_flagged_issues) booleans.
    """
    approved = False
    flagged = False
    for output in agent_outputs:
        if any(kw in output for kw in _AVIARY_APPROVAL_KEYWORDS):
            approved = True
        if any(kw in output for kw in _AVIARY_ISSUE_KEYWORDS):
            flagged = True
    return approved, flagged

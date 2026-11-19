"""Validation 2 figures (8 combinations x 2 iterations), from the tables val2_extract.py writes.

Items (one folder each under --out; every figure as PNG 300 dpi + PDF at a fixed size, plotted data as CSV beside it):
  01_fuel_gtow        slope plots iteration 1 -> 2 (all combos, per structure, every flown mission), fuel and GTOW
  02_parameters       baseline -> last flown design: heatmap of % change, dumbbells, small multiples of % change
  03_gradient         fuel change per flown change (mission k -> k+1): waterfalls, diverging bars, scatter, kg per AR
  04_coupling_flow    coupled values along the tool chain, one figure per combination (x = tool-call order or time)
  05_coupled_response coupled response to each design change: matrix of % change, response vs delta AR

Style follows plots.py (STRUCT_COLORS, HANDLER_LS, HANDLER_MARKERS) and the plotting guidelines: no titles, no
annotations except data labels in matrices, dashed neutral baseline, dotted requirement line.

Usage (aipexws8, Avion venv, nice 15):
    python -m analysis.link20.val2_figures --data /data/Jessica/final_val2/figures/data --out /data/Jessica/final_val2/figures
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import TwoSlopeNorm  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from analysis.link20.plots import HANDLER_LS, HANDLER_MARKERS, STRUCT_COLORS, setup_style  # noqa: E402
from analysis.link20.val2_extract import (AVIARY_DESIGN, BASELINE_FUEL_KG, BASELINE_GTOW_KG, COMBO_ORDER,  # noqa: E402
                                          CYCLE_DESIGN, HANDLER, STRUCT)

FUEL_LIMIT_KG = 15000.0
GTOW_LIMIT_KG = 90000.0
BASE_STYLE = {"color": "0.45", "linestyle": "--", "linewidth": 1.2, "zorder": 1}
LIMIT_STYLE = {"color": "0.45", "linestyle": ":", "linewidth": 1.4, "zorder": 1}
LAYOUT = [["S_SP", "S_IF", "S_GR"], ["O_SP", "O_IF", "O_GR"], [None, "N_IF", "N_GR"]]
STRUCTURES = ["sequential", "orchestrated", "networked"]
CMAP = "RdBu_r"          # sign maps: blue = decrease, red = increase (not a category colour)

LABEL = {
    "wing_area_m2": "Wing area (m²)", "wing_AR": "Wing aspect ratio", "wing_sweep_deg": "Wing sweep (deg)",
    "wing_taper": "Wing taper ratio", "wing_tc": "Wing t/c",
    "htail_area_m2": "H-tail area (m²)", "htail_AR": "H-tail AR", "htail_taper": "H-tail taper", "htail_tc": "H-tail t/c",
    "vtail_area_m2": "V-tail area (m²)", "vtail_AR": "V-tail AR", "vtail_taper": "V-tail taper", "vtail_tc": "V-tail t/c",
    "fus_length_m": "Fuselage length (m)", "fus_height_m": "Fuselage height (m)", "fus_width_m": "Fuselage width (m)",
    "T4_MAX_degR": "T4 max (°R)", "T4_TAKEOFF_degR": "T4 take-off (°R)", "T4_CLIMB_degR": "T4 climb (°R)",
    "BPR": "Bypass ratio", "fan_PR": "Fan PR", "lpc_PR": "LPC PR", "hpc_PR": "HPC PR",
    "wing_mass_kg": "Wing mass (kg)", "mass_mcp_wing_kg": "Wing mass, mass-mcp (kg)",
    "mass_mcp_mtom_sized_kg": "Sized MTOM, mass-mcp (kg)", "mtom_sized_kg": "Take-off mass flown (kg)",
    "cruise_LD": "Cruise L/D", "fn_des_lbf": "Fn_DES (lbf)", "engine_scale": "Engine scale",
    "gtow_kg": "GTOW (kg)", "fuel_kg": "Fuel burn (kg)",
}
SHORT = {"wing_area_m2": "S", "wing_AR": "AR", "wing_sweep_deg": "sweep", "wing_taper": "taper", "wing_tc": "t/c",
         "fus_length_m": "fus. L", "BPR": "BPR", "T4_MAX_degR": "T4", "fan_PR": "fan PR", "hpc_PR": "HPC PR",
         "lpc_PR": "LPC PR"}
DESIGN = list(AVIARY_DESIGN) + list(CYCLE_DESIGN)
COUPLED_END = ["wing_mass_kg", "mtom_sized_kg", "cruise_LD", "fn_des_lbf", "engine_scale", "gtow_kg", "fuel_kg"]
RESPONSE = ["wing_mass_kg", "mass_mcp_mtom_sized_kg", "mtom_sized_kg", "cruise_LD", "fn_des_lbf", "engine_scale",
            "gtow_kg", "fuel_kg"]


def sh(combo: str) -> tuple[str, str]:
    return STRUCT[combo[0]], HANDLER[combo[2:]]


def color(combo: str) -> str:
    return STRUCT_COLORS[sh(combo)[0]]


def kg_fmt(ax, axis="y"):
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(mticker.StrMethodFormatter("{x:,.0f}"))


def save(fig, out: Path, stem: str, data: pd.DataFrame | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"{stem}.png", dpi=300)
    fig.savefig(out / f"{stem}.pdf")
    plt.close(fig)
    if data is not None:
        data.to_csv(out / f"{stem}.csv", index=False)
    print("  ", out / f"{stem}.png")


def combo_handles(combos=COMBO_ORDER) -> list:
    return [Line2D([], [], color=color(c), linestyle=HANDLER_LS[sh(c)[1]], marker=HANDLER_MARKERS[sh(c)[1]],
                   markersize=6, linewidth=1.6, label=c) for c in combos]


def ref_handles(base_label: str, limit_label: str) -> list:
    return [Line2D([], [], label=base_label, **{k: v for k, v in BASE_STYLE.items() if k != "zorder"}),
            Line2D([], [], label=limit_label, **{k: v for k, v in LIMIT_STYLE.items() if k != "zorder"})]


# =============================================================== 01 fuel / GTOW slope plots

METRIC = {"fuel_kg": ("Fuel burn (kg)", BASELINE_FUEL_KG, FUEL_LIMIT_KG, "fuel limit (15,000 kg)", (13900, 16000)),
          "gtow_kg": ("GTOW (kg)", BASELINE_GTOW_KG, GTOW_LIMIT_KG, "GTOW limit (90,000 kg)", (88500, 90500))}
DODGE = {c: (i - 3.5) * 0.025 for i, c in enumerate(COMBO_ORDER)}   # horizontal offset so equal paths stay visible


def slope_table(links: pd.DataFrame, col: str) -> pd.DataFrame:
    base = METRIC[col][1]
    rows = []
    for c in COMBO_ORDER:
        rows.append({"combo": c, "x": 0, "stage": "baseline", col: base})
        for it in (1, 2):
            v = links[(links.combo == c) & (links.iteration == it)][col]
            rows.append({"combo": c, "x": it, "stage": f"iteration {it}", col: float(v.iloc[0]) if len(v) else np.nan})
    return pd.DataFrame(rows)


def slope_all(links, col, out, zero_axis=False):
    label, base, limit, limlab, ylim = METRIC[col]
    t = slope_table(links, col)
    fig, ax = plt.subplots(figsize=(7.6, 4.6))
    for c in COMBO_ORDER:
        g = t[t.combo == c]
        h = sh(c)[1]
        ax.plot(g.x + DODGE[c], g[col], color=color(c), linestyle=HANDLER_LS[h], marker=HANDLER_MARKERS[h],
                markersize=6, linewidth=1.6, zorder=3)
    ax.axhline(base, **BASE_STYLE)
    ax.axhline(limit, **LIMIT_STYLE)
    ax.set_xticks([0, 1, 2], ["Baseline", "Iteration 1", "Iteration 2"])
    ax.set_xlim(-0.3, 2.3)
    ax.set_ylim(0 if zero_axis else ylim[0], ylim[1] if not zero_axis else ylim[1] * 1.02)
    ax.set_ylabel(label)
    kg_fmt(ax)
    ax.legend(handles=combo_handles() + ref_handles(f"baseline ({base:,.0f} kg)", limlab),
              loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False)
    fig.tight_layout()
    save(fig, out, f"{col.split('_')[0]}_slope_all_combos{'_zero_axis' if zero_axis else ''}", t)


def slope_by_structure(links, col, out):
    label, base, limit, limlab, ylim = METRIC[col]
    t = slope_table(links, col)
    fig, axes = plt.subplots(1, 3, figsize=(11.0, 4.2), sharey=True)
    for ax, s in zip(axes, STRUCTURES):
        combos = [c for c in COMBO_ORDER if STRUCT[c[0]] == s]
        for c in combos:
            g = t[t.combo == c]
            h = sh(c)[1]
            ax.plot(g.x + DODGE[c] * 1.5, g[col], color=color(c), linestyle=HANDLER_LS[h],
                    marker=HANDLER_MARKERS[h], markersize=6, linewidth=1.6, zorder=3, label=c)
        ax.axhline(base, **BASE_STYLE)
        ax.axhline(limit, **LIMIT_STYLE)
        ax.set_xticks([0, 1, 2], ["Baseline", "It. 1", "It. 2"])
        ax.set_xlim(-0.3, 2.3)
        ax.set_ylim(*ylim)
        ax.legend(loc="lower left", frameon=False)
        ax.set_xlabel(s.capitalize())
    axes[0].set_ylabel(label)
    kg_fmt(axes[0])
    fig.tight_layout()
    save(fig, out, f"{col.split('_')[0]}_slope_by_structure", t)


def missions_panels(missions, col, out):
    label, base, limit, limlab, ylim = METRIC[col]
    fig, axes = plt.subplots(3, 3, figsize=(11.0, 8.4), sharey=True)
    data = []
    for r, row in enumerate(LAYOUT):
        for cc, c in enumerate(row):
            ax = axes[r][cc]
            if c is None:
                ax.axis("off")
                continue
            g = missions[missions.combo == c].sort_values(["iteration", "mission"]).reset_index(drop=True)
            x = np.arange(len(g)) + 1
            ax.plot(x, g[col], color=color(c), linewidth=1.4, zorder=2)
            for it, face in ((1, color(c)), (2, "white")):
                m = g.iteration == it
                ax.scatter(x[m], g[col][m], facecolor=face, edgecolor=color(c), s=30, linewidth=1.3, zorder=3)
            n1 = int((g.iteration == 1).sum())
            ax.axvline(n1 + 0.5, color="0.75", linewidth=0.9, zorder=0)
            ax.axhline(base, **BASE_STYLE)
            ax.axhline(limit, **LIMIT_STYLE)
            ax.set_title(c, fontsize=12)
            ax.set_xlim(0.3, len(g) + 0.7)
            ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True, nbins=6))
            ax.set_ylim(*ylim)
            if r == 2 or (r == 1 and cc == 0):
                ax.set_xlabel("Flown mission")
            if cc == 0 or (r == 2 and cc == 1):
                ax.set_ylabel(label)
                ax.tick_params(labelleft=True)
            kg_fmt(ax)
            data.append(g[["combo", "iteration", "mission", "seq", col, "converged"]])
    handles = [Line2D([], [], marker="o", linestyle="", color="0.3", label="Iteration 1"),
               Line2D([], [], marker="o", linestyle="", markerfacecolor="white", markeredgecolor="0.3",
                      label="Iteration 2")]
    handles += [Line2D([], [], color=STRUCT_COLORS[s], linewidth=2, label=s.capitalize()) for s in STRUCTURES]
    handles += ref_handles(f"Baseline ({base:,.0f} kg)", limlab[0].upper() + limlab[1:])
    axes[2][0].legend(handles=handles, loc="center", frameon=False)
    fig.tight_layout()
    save(fig, out, f"{col.split('_')[0]}_every_mission_panels", pd.concat(data))


def summary_strip(links, col, out):
    """Summary before trend (rule 6): both iterations per combination and their mean."""
    label, base, limit, limlab, ylim = METRIC[col]
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    rows = []
    for i, c in enumerate(COMBO_ORDER):
        g = links[links.combo == c].sort_values("iteration")
        v = g[col].to_numpy(dtype=float)
        ax.scatter([i - 0.08, i + 0.08][:len(v)], v, color=color(c), marker=HANDLER_MARKERS[sh(c)[1]], s=34,
                   zorder=3)
        ax.plot([i - 0.25, i + 0.25], [v.mean()] * 2, color="black", linewidth=2.0, zorder=4)
        rows.append({"combo": c, "iteration_1": v[0], "iteration_2": v[1], "mean": v.mean()})
    ax.axhline(base, **BASE_STYLE)
    ax.axhline(limit, **LIMIT_STYLE)
    ax.set_xticks(range(len(COMBO_ORDER)), COMBO_ORDER)
    ax.set_ylim(*ylim)
    ax.set_ylabel(label)
    kg_fmt(ax)
    ax.legend(handles=[Line2D([], [], color="black", linewidth=2, label="mean of 2 iterations")]
              + ref_handles(f"baseline ({base:,.0f} kg)", limlab), loc="center left", bbox_to_anchor=(1.01, 0.5),
              frameon=False)
    fig.tight_layout()
    save(fig, out, f"{col.split('_')[0]}_summary_by_combo", pd.DataFrame(rows))


# =============================================================== 02 parameters baseline -> end

def baseline_end(missions: pd.DataFrame) -> pd.DataFrame:
    cols = DESIGN + COUPLED_END
    base = missions[(missions.iteration == 1) & (missions.mission == 1)]
    ref = base[cols].iloc[0]
    for c in cols:   # every combination's first mission is the same baseline mission; refuse otherwise
        spread = (base[c].max() - base[c].min()) / max(abs(ref[c]), 1e-12)
        if spread > 1e-9:
            raise SystemExit(f"baseline missions differ in {c}: {base[c].tolist()}")
    rows = []
    for combo in COMBO_ORDER:
        g = missions[missions.combo == combo].sort_values(["iteration", "mission"])
        end = g.iloc[-1]
        for c in cols:
            rows.append({"combo": combo, "parameter": c, "baseline": ref[c], "end": end[c],
                         "end_iteration": int(end.iteration), "end_mission": int(end.mission),
                         "pct_change": 100.0 * (end[c] - ref[c]) / ref[c]})
    return pd.DataFrame(rows)


def changed_params(be: pd.DataFrame, cols) -> list[str]:
    return [c for c in cols if (be[be.parameter == c]["pct_change"].abs() > 0.1).any()]


def heatmap(be: pd.DataFrame, cols, out, stem, annotate):
    m = be.pivot(index="combo", columns="parameter", values="pct_change").loc[COMBO_ORDER, cols]
    vmax = max(1.0, float(np.nanmax(np.abs(m.to_numpy()))))
    w = max(6.5, 0.42 * len(cols) + 2.6) if not annotate else max(6.5, 0.85 * len(cols) + 2.4)
    fig, ax = plt.subplots(figsize=(w, 4.6 if not annotate else 4.4))
    im = ax.imshow(m.to_numpy(), cmap=CMAP, norm=TwoSlopeNorm(0.0, -vmax, vmax), aspect="auto")
    ax.set_yticks(range(len(m)), m.index)
    ax.set_xticks(range(len(cols)), [LABEL[c] for c in cols], rotation=60, ha="right", rotation_mode="anchor")
    for t in ax.get_yticklabels():
        t.set_color(color(t.get_text()))
    if annotate:
        for i in range(m.shape[0]):
            for j in range(m.shape[1]):
                v = m.iat[i, j]
                ax.text(j, i, "0" if abs(v) < 0.05 else f"{v:+.1f}", ha="center", va="center", fontsize=9,
                        color="white" if abs(v) > 0.6 * vmax else "black")
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    cb = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
    cb.set_label("Change from baseline (%)")
    cb.outline.set_visible(False)
    fig.tight_layout()
    save(fig, out, stem, m.reset_index())


def panel_grid(n, ncols=3, w=3.6, h=2.9):
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(w * ncols, h * nrows), squeeze=False)
    for k in range(n, nrows * ncols):
        axes.flat[k].axis("off")
    return fig, axes


def dumbbells(be, cols, out):
    fig, axes = panel_grid(len(cols))
    y = np.arange(len(COMBO_ORDER))[::-1]
    for ax, c in zip(axes.flat, cols):
        g = be[be.parameter == c].set_index("combo").loc[COMBO_ORDER]
        for yy, combo in zip(y, COMBO_ORDER):
            b, e = g.loc[combo, "baseline"], g.loc[combo, "end"]
            ax.plot([b, e], [yy, yy], color="0.7", linewidth=1.6, zorder=1)
            ax.scatter(e, yy, color=color(combo), marker=HANDLER_MARKERS[sh(combo)[1]], s=38, zorder=3)
        ax.axvline(g.baseline.iloc[0], **BASE_STYLE)
        ax.set_yticks(y, COMBO_ORDER)
        for t in ax.get_yticklabels():
            t.set_color(color(t.get_text()))
        ax.set_xlabel(LABEL[c])
        ax.xaxis.set_major_locator(mticker.MaxNLocator(4))
        if abs(g.baseline.iloc[0]) >= 1000:
            kg_fmt(ax, "x")
    for ax in axes[:, 1:].flat:
        ax.set_yticklabels([])
    fig.tight_layout()
    save(fig, out, "parameters_dumbbell_original_units", be[be.parameter.isin(cols)])


def pct_small_multiples(be, cols, out):
    fig, axes = panel_grid(len(cols))
    y = np.arange(len(COMBO_ORDER))[::-1]
    lim = float(be[be.parameter.isin(cols)]["pct_change"].abs().max()) * 1.1
    for ax, c in zip(axes.flat, cols):
        g = be[be.parameter == c].set_index("combo").loc[COMBO_ORDER]
        ax.barh(y, g["pct_change"], color=[color(k) for k in COMBO_ORDER], height=0.7)
        ax.axvline(0, color="0.3", linewidth=0.8)
        ax.set_xlim(-lim, lim)
        ax.set_yticks(y, COMBO_ORDER)
        for t in ax.get_yticklabels():
            t.set_color(color(t.get_text()))
        ax.set_xlabel(f"{LABEL[c].split(' (')[0]} (% change)")
    for ax in axes[:, 1:].flat:
        ax.set_yticklabels([])
    fig.tight_layout()
    save(fig, out, "parameters_pct_change_small_multiples", be[be.parameter.isin(cols)])


# =============================================================== 03 gradient: fuel change per flown change

def step_label(r) -> str:
    if r.n_params_changed == 0:
        return "mass basis carried" if r.across_iterations else "re-flight, no change"
    parts = []
    for p in str(r.params_changed).split(";"):
        a, b = getattr(r, f"{p}_from"), getattr(r, f"{p}_to")
        nd = 0 if abs(a) >= 100 else 1 if abs(a) >= 20 else 2
        parts.append(f"{SHORT.get(p, p)} {a:.{nd}f}→{b:.{nd}f}")
    return ", ".join(parts)


def step_id(r) -> str:
    return f"{r.combo} {r.from_iteration}.{r.from_mission}→{r.to_iteration}.{r.to_mission}"


def waterfall(steps, missions, combo, out):
    g = steps[steps.combo == combo].reset_index(drop=True)
    m = missions[missions.combo == combo].sort_values(["iteration", "mission"]).reset_index(drop=True)
    labels = ["baseline\n(1.1)"] + [f"{r.to_iteration}.{r.to_mission}\n{step_label(r)}" for r in g.itertuples()]
    fig, ax = plt.subplots(figsize=(max(5.2, 0.62 * len(labels) + 2.2), 4.6))
    col = color(combo)
    start = float(m.fuel_kg.iloc[0])
    ax.bar(0, start - 13800, bottom=13800, color="0.75", width=0.6)
    prev = start
    rows = [{"step": "baseline", "fuel_from": None, "fuel_to": start, "delta_kg": None}]
    for k, r in enumerate(g.itertuples(), 1):
        d = r.d_fuel_kg
        if abs(d) < 20:      # below a bar's visible height: draw a tick at the new level (value in the CSV)
            ax.plot([k - 0.3, k + 0.3], [prev, prev], color=col, linewidth=1.6)
        else:
            ax.bar(k, d, bottom=prev, width=0.6, color=col if d < 0 else "white", edgecolor=col, linewidth=1.4,
                   hatch=None if d < 0 else "///")
        ax.plot([k - 1 + 0.3, k - 0.3], [prev, prev], color="0.6", linewidth=0.7)
        rows.append({"step": step_id(r), "change": step_label(r), "n_params_changed": r.n_params_changed,
                     "fuel_from": prev, "fuel_to": prev + d, "delta_kg": d})
        prev += d
    n1 = int((m.iteration == 1).sum())
    ax.axvline(n1 - 0.5, color="0.75", linewidth=0.9, zorder=0)
    ax.axhline(BASELINE_FUEL_KG, **BASE_STYLE)
    ax.axhline(FUEL_LIMIT_KG, **LIMIT_STYLE)
    ax.set_xticks(range(len(labels)), labels, rotation=90, fontsize=9)
    ax.set_ylim(13800, 16000)
    ax.set_ylabel("Fuel burn (kg)")
    kg_fmt(ax)
    ax.legend(handles=[Patch(facecolor=col, label="decrease"),
                       Patch(facecolor="white", edgecolor=col, hatch="///", label="increase")]
              + ref_handles("baseline", "fuel limit"), loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False, fontsize=10)
    fig.tight_layout()
    save(fig, out, f"waterfall_{combo}", pd.DataFrame(rows))


def informative(steps):
    """Steps where a flown input changed (design, mass basis or scale); identical re-flights are left out."""
    same = (steps.n_params_changed == 0) & (steps.d_fuel_kg.abs() < 1e-3)
    return steps[~same].copy(), int(same.sum())


def diverging_bar(steps, out):
    s, n_same = informative(steps)
    s = s.iloc[::-1].reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(8.4, 0.36 * len(s) + 1.4))
    y = np.arange(len(s))
    for k, r in s.iterrows():
        c = color(r.combo)
        multi = r.n_params_changed > 1
        ax.barh(k, r.d_fuel_kg, color="white" if multi else c, edgecolor=c, hatch="///" if multi else None,
                linewidth=1.2, height=0.7)
    ax.axvline(0, color="0.3", linewidth=0.8)
    ax.set_yticks(y, [f"{step_id(r)}: {step_label(r)}" for _, r in s.iterrows()], fontsize=10)
    for t, (_, r) in zip(ax.get_yticklabels(), s.iterrows()):
        t.set_color(color(r.combo))
    ax.set_xlabel("Fuel change (kg)")
    kg_fmt(ax, "x")
    ax.legend(handles=[Patch(facecolor=STRUCT_COLORS[st], label=st.capitalize()) for st in STRUCTURES]
              + [Patch(facecolor="white", edgecolor="0.3", hatch="///", label="several parameters at once")],
              loc="lower left", frameon=False, fontsize=10)
    fig.tight_layout()
    cols = ["combo", "from_iteration", "from_mission", "to_iteration", "to_mission", "n_params_changed",
            "params_changed", "change_label", "d_fuel_kg", "pct_fuel_kg"]
    save(fig, out, "fuel_change_per_step_diverging", s[cols].iloc[::-1])
    return n_same


def scatter_param_vs_fuel(steps, out):
    s, _ = informative(steps)
    panels = [p for p in DESIGN if (s[f"{p}_to"] - s[f"{p}_from"]).abs().gt(1e-3 * s[f"{p}_from"].abs()).any()]
    panels.append("mtom_sized_kg")
    fig, axes = plt.subplots(1, len(panels), figsize=(3.9 * len(panels), 3.9), sharey=True, squeeze=False)
    rows = []
    for ax, p in zip(axes[0], panels):
        dp = s[f"{p}_to"] - s[f"{p}_from"]
        if p == "mtom_sized_kg":
            mask = s.n_params_changed == 0                # take-off mass basis moved, no design parameter did
        else:
            mask = dp.abs() > 1e-3 * s[f"{p}_from"].abs()
        for k, r in s[mask].iterrows():
            h = sh(r.combo)[1]
            multi = r.n_params_changed > 1
            span = float(dp[mask].abs().max() or 1.0)
            ax.scatter(dp[k] + DODGE[r.combo] * 0.6 * span, r.d_fuel_kg, marker=HANDLER_MARKERS[h], s=46, linewidth=1.3,
                       facecolor="white" if multi else color(r.combo), edgecolor=color(r.combo), zorder=3)
            rows.append({"panel": p, "combo": r.combo, "step": step_id(r), "change": step_label(r),
                         "d_param": dp[k], "d_fuel_kg": r.d_fuel_kg, "several_params": multi})
        ax.axhline(0, color="0.3", linewidth=0.8)
        ax.axvline(0, color="0.8", linewidth=0.8)
        ax.set_xlabel("Δ " + (LABEL[p] if p != "mtom_sized_kg" else "take-off mass, no design change (kg)"))
    axes[0][0].set_ylabel("Fuel change (kg)")
    kg_fmt(axes[0][0])
    present = sorted({r["combo"] for r in rows}, key=COMBO_ORDER.index)
    axes[0][-1].legend(handles=[Line2D([], [], color=color(c), marker=HANDLER_MARKERS[sh(c)[1]], linestyle="",
                                       label=c) for c in present]
                       + [Line2D([], [], marker="o", linestyle="", markerfacecolor="white", markeredgecolor="0.3",
                                 label="several at once")],
                       loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False)
    fig.tight_layout()
    save(fig, out, "param_delta_vs_fuel_delta_scatter", pd.DataFrame(rows))


def fuel_per_ar(steps, out):
    s = steps[steps.params_changed == "wing_AR"].reset_index(drop=True)
    s["kg_per_unit_AR"] = s.d_fuel_kg / (s.wing_AR_to - s.wing_AR_from)
    fig, ax = plt.subplots(figsize=(7.2, 0.42 * len(s) + 1.4))
    y = np.arange(len(s))[::-1]
    ax.barh(y, s.kg_per_unit_AR, color=[color(c) for c in s.combo], height=0.7)
    ax.axvline(0, color="0.3", linewidth=0.8)
    ax.set_yticks(y, [f"{step_id(r)}: AR {r.wing_AR_from:.2f}→{r.wing_AR_to:.2f}" for r in s.itertuples()],
                  fontsize=10)
    for t, c in zip(ax.get_yticklabels(), s.combo):
        t.set_color(color(c))
    ax.set_xlabel("Fuel change per unit aspect ratio (kg)")
    kg_fmt(ax, "x")
    fig.tight_layout()
    save(fig, out, "fuel_change_per_unit_AR_single_parameter_steps",
         s[["combo", "from_iteration", "from_mission", "to_iteration", "to_mission", "wing_AR_from", "wing_AR_to",
            "d_fuel_kg", "kg_per_unit_AR"]])


# =============================================================== 04 coupled values along the tool chain

LANES = [("geometry", "geo_AR", "Wing AR\n(tigl)"), ("geometry", "geo_area", "Wing area m²\n(tigl)"),
         ("mass", "mass_wing_kg", "Wing mass kg\n(mass-mcp)"),
         ("mass", "mtom_sized_kg", "Sized MTOM kg\n(mass-mcp)"),
         ("cruise_point", "cruise_LD", "Cruise L/D\n(aviary)"), ("pycycle", "fn_des_lbf", "Fn_DES lbf\n(pyCycle)"),
         ("scale", "engine_scale", "Engine scale\n(aviary)"), ("mission", "fuel_kg", "Fuel kg\n(aviary)")]


def flow_figure(events, links, combo, out, xmode):
    e = events[(events.combo == combo)].copy()
    lk = links[links.combo == combo].set_index("iteration")
    gap = 6
    if xmode == "order":
        off2 = int(lk.loc[1, "n_rows"]) + gap
        e["x"] = e.row + np.where(e.iteration == 2, off2, 0)
        xlabel, ends = "Tool call (order in the run)", (int(lk.loc[1, "n_rows"]), off2 + int(lk.loc[2, "n_rows"]))
    else:
        d1 = float(lk.loc[1, "duration_min"])
        off2 = d1 + max(3.0, 0.04 * (d1 + float(lk.loc[2, "duration_min"])))
        e["x"] = e.t_min + np.where(e.iteration == 2, off2, 0.0)
        xlabel, ends = "Time (min)", (d1, off2 + float(lk.loc[2, "duration_min"]))
    # engine scale: the SCALE_FACTOR set in aviary and the one each mission flew
    e["scale_lane"] = e.engine_scale.where(e.lane.isin(["aviary_inputs", "mission"]))
    lanes = []
    for lane, col, lab in LANES:
        if col == "geo_area":
            v = e[e.lane == "geometry"][col].dropna()
            if v.empty or (v.max() - v.min()) / v.mean() < 1e-3:
                continue        # area lane only where the wing area changed
        lanes.append((lane, col, lab))
    fig, axes = plt.subplots(len(lanes), 1, figsize=(10.0, 1.05 * len(lanes) + 1.0), sharex=True)
    c = color(combo)
    rows = []
    for ax, (lane, col, lab) in zip(axes, lanes):
        if col == "engine_scale":
            g = e[e.scale_lane.notna()][["x", "iteration", "row", "tool", "scale_lane"]].rename(
                columns={"scale_lane": "value"})
        elif lane == "mass" and col == "mtom_sized_kg":
            g = e[(e.lane == "mass")][["x", "iteration", "row", "tool", "mtom_sized_kg"]].rename(
                columns={"mtom_sized_kg": "value"})
            tko = e[(e.tool == "configure_mission")][["x", "iteration", "row", "tool", "takeoff_mass_kg"]].rename(
                columns={"takeoff_mass_kg": "value"})
            ax.scatter(tko.x, tko.value, marker="x", color="0.35", s=26, linewidth=1.2, zorder=4)
            rows += [{"lane": "take-off mass (configure_mission)", **r} for r in tko.to_dict("records")]
        else:
            g = e[e.lane == lane][["x", "iteration", "row", "tool", col]].rename(columns={col: "value"})
        g = g.dropna(subset=["value"]).sort_values("x")
        vals = g.value.to_numpy()   # changed = differs from the previous value in this lane (across iterations); first = no
        changed_all = pd.Series(np.r_[False, np.abs(np.diff(vals)) > 1e-6 * np.abs(vals[:-1])], index=g.index)
        for it in (1, 2):
            gi = g[g.iteration == it]
            if gi.empty:
                continue
            ch = changed_all.loc[gi.index].to_numpy()
            ax.step(gi.x, gi.value, where="post", color=c, linewidth=1.0, alpha=0.6, zorder=2)
            ax.scatter(gi.x[ch], gi.value[ch], color=c, s=24, zorder=3)
            ax.scatter(gi.x[~ch], gi.value[~ch], facecolor="white", edgecolor=c, s=24, linewidth=1.1, zorder=3)
            rows += [{"lane": lab.replace("\n", " "), **r, "changed": bool(k)} for r, k in
                     zip(gi.to_dict("records"), ch)]
        if col == "fuel_kg":
            ax.axhline(BASELINE_FUEL_KG, **BASE_STYLE)
            ax.axhline(FUEL_LIMIT_KG, **LIMIT_STYLE)
        ax.axvline(ends[0] + (gap / 2 if xmode == "order" else 0.0), color="0.75", linewidth=0.9, zorder=0)
        ax.set_ylabel(lab, rotation=0, ha="right", va="center", fontsize=10)
        ax.yaxis.set_major_locator(mticker.MaxNLocator(3))
        vv = g.value.to_numpy()
        if len(vv) and col != "fuel_kg" and np.ptp(vv) < 0.005 * abs(vv.mean()):   # flat lane: +-0.5 % window
            ax.set_ylim(vv.mean() * 0.995, vv.mean() * 1.005)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(
            lambda v, _p: f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.2f}" if abs(v) >= 10 else f"{v:.3f}"))
        ax.tick_params(labelsize=9)
    axes[-1].set_xlabel(xlabel)
    axes[-1].set_xlim(-1, ends[1] + 1)
    top = axes[0].secondary_xaxis("top")
    mid2 = (ends[0] + (gap if xmode == "order" else 0) + ends[1]) / 2
    top.set_xticks([ends[0] / 2, mid2], ["Iteration 1", "Iteration 2"])
    top.tick_params(length=0)
    top.spines["top"].set_visible(False)
    handles = [Line2D([], [], marker="o", linestyle="", color=c, label="changed from the previous value"),
               Line2D([], [], marker="o", linestyle="", markerfacecolor="white", markeredgecolor=c,
                      label="unchanged or first value"),
               Line2D([], [], marker="x", linestyle="", color="0.35", label="take-off mass flown")]
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 0.0), fontsize=10)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    save(fig, out, f"coupling_flow_{combo}_{xmode}", pd.DataFrame(rows))


# =============================================================== 05 coupled response to a design change

def response_matrix(steps, out, include_no_change):
    s = steps[steps.n_params_changed > 0].copy()
    if include_no_change:
        s = pd.concat([s, steps[(steps.n_params_changed == 0) & steps.across_iterations]])
    s = s.reset_index(drop=True)
    m = pd.DataFrame({LABEL[c]: s[f"pct_{c}"] for c in RESPONSE})
    m.index = [f"{step_id(r)}: {step_label(r)}" for r in s.itertuples()]
    a = m.to_numpy(dtype=float)
    vmax = float(np.nanmax(np.abs(a)))
    fig, ax = plt.subplots(figsize=(11.0, 0.42 * len(m) + 2.3))
    im = ax.imshow(np.ma.masked_invalid(a), cmap=CMAP, norm=TwoSlopeNorm(0.0, -vmax, vmax), aspect="auto")
    ax.set_facecolor("0.92")
    for i in range(a.shape[0]):
        for j in range(a.shape[1]):
            v = a[i, j]
            txt = "not run" if np.isnan(v) else "0" if abs(v) < 0.005 else f"{v:+.2f}" if abs(v) < 0.1 else f"{v:+.1f}"
            ax.text(j, i, txt, ha="center", va="center", fontsize=9,
                    color="0.4" if np.isnan(v) else "white" if abs(v) > 0.6 * vmax else "black")
    ax.set_xticks(range(a.shape[1]), m.columns, rotation=30, ha="right", rotation_mode="anchor")
    ax.set_yticks(range(len(m)), m.index, fontsize=10)
    for t, c in zip(ax.get_yticklabels(), s.combo):
        t.set_color(color(c))
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(length=0)
    cb = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cb.set_label("Change (%)")
    cb.outline.set_visible(False)
    fig.tight_layout()
    stem = "coupled_response_matrix" + ("_with_mass_basis_steps" if include_no_change else "")
    data = s[["combo", "from_iteration", "from_mission", "to_iteration", "to_mission", "params_changed",
              "change_label"] + [f"{p}_{k}" for c in RESPONSE for p, k in ((c, "from"), (c, "to"))]
             + [f"pct_{c}" for c in RESPONSE]]
    save(fig, out, stem, data)


def response_vs_dar(steps, out):
    s = steps[steps.params_changed.fillna("").str.contains("wing_AR")].copy()
    s["dAR"] = s.wing_AR_to - s.wing_AR_from
    panels = [c for c in RESPONSE if c != "mtom_sized_kg"]   # take-off mass flown does not move within an iteration
    fig, axes = panel_grid(len(panels), ncols=4, w=3.1, h=2.8)
    for ax, c in zip(axes.flat, panels):
        for _, r in s.iterrows():
            multi = r.n_params_changed > 1
            ax.scatter(r.dAR + DODGE[r.combo] * 0.15, r[f"pct_{c}"], marker=HANDLER_MARKERS[sh(r.combo)[1]], s=40, linewidth=1.2,
                       facecolor="white" if multi else color(r.combo), edgecolor=color(r.combo), zorder=3)
        ax.axhline(0, color="0.3", linewidth=0.8)
        ax.set_ylabel(f"{LABEL[c].split(' (')[0]} (%)", fontsize=10)
        ax.set_xlabel("Δ wing AR", fontsize=10)
        ax.tick_params(labelsize=9)
    present = sorted(set(s.combo), key=COMBO_ORDER.index)
    fig.legend(handles=[Line2D([], [], color=color(c), marker=HANDLER_MARKERS[sh(c)[1]], linestyle="", label=c)
                        for c in present]
               + [Line2D([], [], marker="o", linestyle="", markerfacecolor="white", markeredgecolor="0.3",
                         label="AR and area changed at once")],
               loc="lower center", ncol=len(present) + 1, frameon=False, fontsize=10)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    save(fig, out, "coupled_response_vs_delta_AR",
         s[["combo", "from_iteration", "from_mission", "to_iteration", "to_mission", "params_changed", "dAR"]
           + [f"pct_{c}" for c in RESPONSE]])


# =============================================================== main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    setup_style()
    missions = pd.read_csv(args.data / "missions.csv")
    links = pd.read_csv(args.data / "links.csv")
    steps = pd.read_csv(args.data / "steps.csv")
    events = pd.read_csv(args.data / "events.csv")

    o1 = args.out / "01_fuel_gtow"
    for col in ("fuel_kg", "gtow_kg"):
        summary_strip(links, col, o1)
        slope_all(links, col, o1)
        slope_by_structure(links, col, o1)
        missions_panels(missions, col, o1)
    slope_all(links, "fuel_kg", o1, zero_axis=True)

    o2 = args.out / "02_parameters"
    be = baseline_end(missions)
    heatmap(be, DESIGN + COUPLED_END, o2, "parameters_pct_change_heatmap_all", annotate=False)
    ch = changed_params(be, DESIGN + COUPLED_END)
    heatmap(be, ch, o2, "parameters_pct_change_heatmap_changed_annotated", annotate=True)
    dumbbells(be, ch, o2)
    pct_small_multiples(be, ch, o2)
    be.to_csv(o2 / "baseline_vs_end_all_parameters.csv", index=False)

    o3 = args.out / "03_gradient"
    for c in COMBO_ORDER:
        waterfall(steps, missions, c, o3)
    n_same = diverging_bar(steps, o3)
    scatter_param_vs_fuel(steps, o3)
    fuel_per_ar(steps, o3)
    print("identical re-flights left out of the diverging bar / scatter:", n_same)

    o4 = args.out / "04_coupling_flow"
    for c in COMBO_ORDER:
        for xmode in ("order", "time"):
            flow_figure(events, links, c, o4, xmode)

    o5 = args.out / "05_coupled_response"
    response_matrix(steps, o5, include_no_change=False)
    response_matrix(steps, o5, include_no_change=True)
    response_vs_dar(steps, o5)


if __name__ == "__main__":
    main()

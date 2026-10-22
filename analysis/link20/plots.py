"""Publication plots for the 20-link study, from links.csv (extract.py) and the baseline mission (baseline_mission.py).

Style (Jessica, 2026-10-22): seaborn, white background, PNG, no titles or annotations, axis titles with units,
legend only when needed. Colour = organisational structure. Handlers are told apart three ways, one file each
(line style, marker, panels with shared axes); she picks one. Every metric gets an average plot (strip and box,
one file each) and a line plot over links. Links that did not fly are left out of flight-output averages and are
drawn as empty circles at the bottom of line plots. Links still running are left out.

Usage (aipexws8, Avion venv):
    python -m analysis.link20.plots --links out/links.csv --baseline baseline/baseline_run1.json --out out/plots
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import seaborn as sns  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from analysis.link20 import recommendations as rec  # noqa: E402

STRUCTURES = ["sequential", "orchestrated", "networked"]
HANDLERS = ["staged_pipeline", "iterative_feedback", "graph_routed"]
STRUCT_COLORS = {"sequential": "#0072B2", "orchestrated": "#D55E00", "networked": "#009E73"}
STRUCT_LABELS = {"sequential": "Sequential", "orchestrated": "Orchestrated", "networked": "Networked"}
HANDLER_LABELS = {"staged_pipeline": "Staged pipeline", "iterative_feedback": "Iterative feedback",
                  "graph_routed": "Graph-routed"}
HANDLER_SHORT = {"staged_pipeline": "SP", "iterative_feedback": "IF", "graph_routed": "GR"}
HANDLER_LS = {"staged_pipeline": "-", "iterative_feedback": "--", "graph_routed": ":"}
HANDLER_MARKERS = {"staged_pipeline": "o", "iterative_feedback": "s", "graph_routed": "^"}
COMBOS = [(s, h) for s in STRUCTURES for h in HANDLERS if (s, h) != ("networked", "staged_pipeline")]
LINE_VARIANTS = ["linestyle", "marker", "panels"]

# Mission limits from the prompts (config/mdo_f25_sequential_agents.yaml, aviary_mdo_f25_networked.yaml).
FUEL_LIMIT_KG = 15000.0
GTOW_LIMIT_KG = 90000.0
REF_STYLE = {"color": "0.35", "linestyle": (0, (5, 3)), "linewidth": 1.1, "zorder": 1}
LIMIT_STYLE = {"color": "#B2182B", "linestyle": (0, (2, 2)), "linewidth": 1.1, "zorder": 1}


def setup_style() -> None:
    sns.set_theme(style="ticks", context="paper", font_scale=1.25,
                  rc={"figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
                      "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
                      "savefig.dpi": 300, "font.family": "DejaVu Sans"})


@dataclass
class Metric:
    key: str                      # file stem
    col: str                      # column in links.csv
    label: str                    # axis title with units
    flight: bool = False          # flight output: did-not-fly links excluded / drawn as empty circles
    structures: tuple = tuple(STRUCTURES)
    handlers: tuple = tuple(HANDLERS)
    refs: list = field(default_factory=list)   # (value, legend label, style)
    scale: float = 1.0
    ordinal: dict | None = None   # value -> tick label (for levels)


def combos_for(m: Metric) -> list[tuple[str, str]]:
    return [(s, h) for s, h in COMBOS if s in m.structures and h in m.handlers]


def series(df: pd.DataFrame, m: Metric) -> pd.DataFrame:
    """Long table: structure, handler, link, value, nofly."""
    d = df[[(s, h) in combos_for(m) for s, h in zip(df.structure, df.handler)]].copy()
    d["value"] = pd.to_numeric(d[m.col], errors="coerce") * m.scale
    d["nofly"] = False
    if m.flight:
        d["nofly"] = ~d["flew"].astype(bool)
        d.loc[d["nofly"], "value"] = np.nan
    return d[["structure", "handler", "link", "value", "nofly"]].sort_values(["structure", "handler", "link"])


def save(fig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print("  ", path)


def ref_handles(m: Metric) -> list:
    return [Line2D([], [], label=lab, **{k: v for k, v in st.items() if k != "zorder"}) for _, lab, st in m.refs]


def draw_refs(ax, m: Metric) -> None:
    for val, _, st in m.refs:
        ax.axhline(val * m.scale, **st)


def apply_ordinal(ax, m: Metric) -> None:
    if m.ordinal:
        ax.set_yticks(list(m.ordinal))
        ax.set_yticklabels(list(m.ordinal.values()))
        ax.set_ylim(min(m.ordinal) - 0.4, max(m.ordinal) + 0.4)


# ---------------------------------------------------------------- average plots

def average_plot(df: pd.DataFrame, m: Metric, kind: str, path: Path) -> None:
    d = series(df, m)
    combos = combos_for(m)
    fig, ax = plt.subplots(figsize=(max(4.2, 1.05 * len(combos) + 1.6), 4.2))
    rng = np.random.default_rng(0)
    for i, (s, h) in enumerate(combos):
        v = d[(d.structure == s) & (d.handler == h)]["value"].dropna().to_numpy()
        col = STRUCT_COLORS[s]
        if not len(v):
            continue
        if kind == "strip":
            ax.scatter(i + rng.uniform(-0.18, 0.18, len(v)), v, s=22, color=col, alpha=0.75,
                       edgecolor="white", linewidth=0.4, zorder=3)
            ax.plot([i - 0.28, i + 0.28], [v.mean()] * 2, color="black", linewidth=2.2, zorder=4)
        else:
            ax.boxplot(v, positions=[i], widths=0.55, patch_artist=True, showmeans=True, showfliers=True,
                       boxprops={"facecolor": col, "alpha": 0.55, "edgecolor": col},
                       medianprops={"color": "black", "linewidth": 1.2},
                       whiskerprops={"color": col}, capprops={"color": col},
                       flierprops={"marker": "o", "markersize": 3.5, "markerfacecolor": col,
                                   "markeredgecolor": col, "alpha": 0.8},
                       meanprops={"marker": "D", "markerfacecolor": "white", "markeredgecolor": "black",
                                  "markersize": 5})
    draw_refs(ax, m)
    ax.set_xticks(range(len(combos)))
    ax.set_xticklabels([HANDLER_LABELS[h].replace(" ", "\n").replace("-", "-\n") for _, h in combos])
    ax.set_xlim(-0.6, len(combos) - 0.4)
    ax.set_ylabel(m.label)
    apply_ordinal(ax, m)
    handles = [Patch(color=STRUCT_COLORS[s], label=STRUCT_LABELS[s]) for s in STRUCTURES if s in m.structures]
    if len(handles) == 1:
        handles = []
    handles.append(Line2D([], [], color="black", linewidth=2.2, label="Mean") if kind == "strip" else
                   Line2D([], [], marker="D", linestyle="", markerfacecolor="white", markeredgecolor="black",
                          label="Mean"))
    handles += ref_handles(m)
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1.0))
    save(fig, path)


# ---------------------------------------------------------------- line plots

def _plot_combo(ax, g: pd.DataFrame, s: str, h: str, variant: str) -> None:
    kw = {"color": STRUCT_COLORS[s], "linewidth": 1.5, "zorder": 3}
    if variant == "linestyle":
        kw.update(linestyle=HANDLER_LS[h], marker="o", markersize=3)
    elif variant == "marker":
        kw.update(linestyle="-", marker=HANDLER_MARKERS[h], markersize=5)
    else:  # "panels", or "single" when a metric covers one handler only
        kw.update(linestyle="-", marker="o", markersize=3.5)
    ax.plot(g.link, g.value, **kw)


def _nofly_markers(ax, items) -> None:
    """Empty circles at the bottom of the axis for links that did not fly (dodged so they don't overlap)."""
    if not items:
        return
    y0 = ax.get_ylim()[0]
    n = len({(s, h) for s, h, _ in items})
    offsets = {k: (i - (n - 1) / 2) * 0.13 for i, k in enumerate(sorted({(s, h) for s, h, _ in items}))}
    for s, h, links in items:
        ax.scatter(np.asarray(links) + offsets[(s, h)], [y0] * len(links), s=34, facecolor="white",
                   edgecolor=STRUCT_COLORS[s], linewidth=1.3, clip_on=False, zorder=5)


def _finish_link_axis(ax) -> None:
    ax.set_xlim(0.5, 20.5)
    ax.set_xticks([1, 5, 10, 15, 20])


def line_plot(df: pd.DataFrame, m: Metric, variant: str, path: Path) -> None:
    d = series(df, m)
    combos = combos_for(m)
    handlers = [h for h in HANDLERS if any(c[1] == h for c in combos)]
    structures = [s for s in STRUCTURES if any(c[0] == s for c in combos)]
    if variant == "panels":
        fig, axes = plt.subplots(1, len(handlers), figsize=(3.6 * len(handlers) + 1.6, 3.8),
                                 sharex=True, sharey=True, squeeze=False)
        axes = list(axes[0])
        ax_of = {h: axes[i] for i, h in enumerate(handlers)}
    else:
        fig, ax = plt.subplots(figsize=(7.2, 4.0))
        axes, ax_of = [ax], {h: ax for h in handlers}
    nofly = {id(a): [] for a in axes}
    for s, h in combos:
        g = d[(d.structure == s) & (d.handler == h)]
        ax = ax_of[h]
        _plot_combo(ax, g, s, h, variant)
        if g.nofly.any():
            nofly[id(ax)].append((s, h, g.loc[g.nofly, "link"].to_numpy()))
    for ax in axes:
        draw_refs(ax, m)
        _finish_link_axis(ax)
        apply_ordinal(ax, m)
    if any(nofly.values()) and not m.ordinal:
        lo = min(a.get_ylim()[0] for a in axes)
        hi = max(a.get_ylim()[1] for a in axes)
        for ax in axes:
            ax.set_ylim(lo, hi)
    for ax in axes:
        _nofly_markers(ax, nofly[id(ax)])
    for ax in axes:
        ax.set_xlabel("Link (iteration)")
    axes[0].set_ylabel(m.label)
    if variant == "panels":
        for h in handlers:
            ax_of[h].set_title(HANDLER_LABELS[h], fontsize=11)

    handles = []
    if len(structures) > 1 or variant == "panels":
        handles += [Line2D([], [], color=STRUCT_COLORS[s], linewidth=2, label=STRUCT_LABELS[s]) for s in structures]
    if variant != "panels" and len(handlers) > 1:
        for h in handlers:
            if variant == "linestyle":
                handles.append(Line2D([], [], color="0.2", linestyle=HANDLER_LS[h], linewidth=1.5,
                                      label=HANDLER_LABELS[h]))
            else:
                handles.append(Line2D([], [], color="0.2", marker=HANDLER_MARKERS[h], linestyle="-",
                                      label=HANDLER_LABELS[h]))
    if any(nofly.values()):
        handles.append(Line2D([], [], marker="o", linestyle="", markerfacecolor="white",
                              markeredgecolor="0.3", markersize=6, label="Did not fly"))
    handles += ref_handles(m)
    if handles:
        axes[-1].legend(handles=handles, loc="upper left", bbox_to_anchor=(1.02, 1.0))
    save(fig, path)


def metric_figures(df, m: Metric, out: Path) -> None:
    for kind in ("strip", "box"):
        average_plot(df, m, kind, out / f"{m.key}_avg_{kind}.png")
    handlers = {h for _, h in combos_for(m)}
    variants = LINE_VARIANTS if len(handlers) > 1 else ["single"]
    for v in variants:
        line_plot(df, m, v, out / f"{m.key}_line_{v}.png" if len(variants) > 1 else out / f"{m.key}_line.png")


# ---------------------------------------------------------------- design parameters

PARAMS = [  # (column, axis title, baseline source key)
    ("Aircraft.Wing.AREA", "Wing area (m²)"),
    ("Aircraft.Wing.ASPECT_RATIO", "Wing aspect ratio (–)"),
    ("Aircraft.Wing.SWEEP", "Wing sweep (deg)"),
    ("Aircraft.Wing.TAPER_RATIO", "Wing taper ratio (–)"),
    ("Aircraft.Fuselage.LENGTH", "Fuselage length (m)"),
    ("Aircraft.Fuselage.MAX_HEIGHT", "Fuselage height (m)"),
    ("Aircraft.Fuselage.MAX_WIDTH", "Fuselage width (m)"),
    ("Aircraft.Engine.SCALE_FACTOR", "Engine scale factor (–)"),
    ("engine.T4_MAX", "Turbine inlet temp. T4 (°R)"),
    ("engine.splitter.BPR", "Bypass ratio (–)"),
    ("engine.fan.PR", "Fan pressure ratio (–)"),
    ("engine.lpc.PR", "LPC pressure ratio (–)"),
    ("engine.hpc.PR", "HPC pressure ratio (–)"),
    ("Mission.Design.LIFT_COEFFICIENT", "Design lift coeff. (–)"),
    ("Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR", "Subsonic drag factor (–)"),
    ("Aircraft.Wing.MASS_SCALER", "Wing mass scaler (–)"),
]
PARAM_GROUPS = {"geometry": PARAMS[0:7], "engine": PARAMS[7:13], "aero_mass": PARAMS[13:16]}


def params_small_multiples(df, refs: dict, variant: str, path: Path) -> None:
    fig, axes = plt.subplots(4, 4, figsize=(15, 11.5), sharex=True)
    for ax, (col, lab) in zip(axes.flat, PARAMS):
        for s, h in COMBOS:
            g = df[(df.structure == s) & (df.handler == h)].sort_values("link")
            _plot_combo(ax, g.assign(value=pd.to_numeric(g[col], errors="coerce")), s, h, variant)
        if refs.get(col) is not None:
            ax.axhline(refs[col], **REF_STYLE)
        ax.set_ylabel(lab)
        _finish_link_axis(ax)
    for ax in axes[-1]:
        ax.set_xlabel("Link (iteration)")
    handles = [Line2D([], [], color=STRUCT_COLORS[s], linewidth=2, label=STRUCT_LABELS[s]) for s in STRUCTURES]
    for h in HANDLERS:
        handles.append(Line2D([], [], color="0.2", linestyle=HANDLER_LS[h] if variant == "linestyle" else "-",
                              marker=None if variant == "linestyle" else HANDLER_MARKERS[h], label=HANDLER_LABELS[h]))
    handles.append(Line2D([], [], label="Baseline (unchanged design)",
                          **{k: v for k, v in REF_STYLE.items() if k != "zorder"}))
    fig.legend(handles=handles, loc="lower center", ncol=7, bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    save(fig, path)


def params_average(df, refs: dict, path: Path) -> None:
    fig, axes = plt.subplots(4, 4, figsize=(15, 11.5))
    rng = np.random.default_rng(0)
    for ax, (col, lab) in zip(axes.flat, PARAMS):
        for i, (s, h) in enumerate(COMBOS):
            v = pd.to_numeric(df[(df.structure == s) & (df.handler == h)][col], errors="coerce").dropna().to_numpy()
            if len(v):
                ax.scatter(i + rng.uniform(-0.18, 0.18, len(v)), v, s=10, color=STRUCT_COLORS[s], alpha=0.7,
                           edgecolor="none", zorder=3)
                ax.plot([i - 0.3, i + 0.3], [v.mean()] * 2, color="black", linewidth=1.8, zorder=4)
        if refs.get(col) is not None:
            ax.axhline(refs[col], **REF_STYLE)
        ax.set_xticks(range(len(COMBOS)))
        ax.set_xticklabels([HANDLER_SHORT[h] for _, h in COMBOS], fontsize=9)
        ax.set_ylabel(lab)
    handles = [Patch(color=STRUCT_COLORS[s], label=STRUCT_LABELS[s]) for s in STRUCTURES]
    handles += [Line2D([], [], color="black", linewidth=1.8, label="Mean"),
                Line2D([], [], label="Baseline (unchanged design)", **{k: v for k, v in REF_STYLE.items() if k != "zorder"}),
                Line2D([], [], linestyle="", label="SP staged pipeline · IF iterative feedback · GR graph-routed")]
    fig.legend(handles=handles, loc="lower center", ncol=6, bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    save(fig, path)


def param_change_table(df, refs: dict, s: str, h: str) -> pd.DataFrame:
    """% change of each parameter from the previous link (link 1: from the baseline). NaN where not recorded."""
    g = df[(df.structure == s) & (df.handler == h)].set_index("link").sort_index()
    out = {}
    for col, lab in PARAMS:
        v = pd.to_numeric(g[col], errors="coerce").reindex(range(1, 21))
        prev = v.shift(1)
        prev.loc[1] = refs.get(col, np.nan)
        out[lab] = 100.0 * (v - prev) / prev.abs()
    return pd.DataFrame(out).T


def params_heatmaps(df, refs: dict, out: Path) -> None:
    tables = {(s, h): param_change_table(df, refs, s, h) for s, h in COMBOS}
    allv = np.concatenate([t.to_numpy().ravel() for t in tables.values()])
    allv = np.abs(allv[np.isfinite(allv)])
    cap = float(np.ceil(np.percentile(allv[allv > 0], 95) / 5) * 5) if (allv > 0).any() else 5.0
    for (s, h), t in tables.items():
        fig, ax = plt.subplots(figsize=(9.5, 6.2))
        sns.heatmap(t, ax=ax, cmap="RdBu_r", center=0, vmin=-cap, vmax=cap, linewidths=0.4, linecolor="white",
                    cbar_kws={"label": "Change from previous link (%)", "extend": "both"})
        ax.set_facecolor("0.92")   # not recorded
        ax.set_xlabel("Link (iteration)")
        ax.set_ylabel("")
        save(fig, out / f"param_change_heatmap_{s}_{h}.png")


# ---------------------------------------------------------------- categorical / special plots

def stop_category(reason) -> str | None:
    if not isinstance(reason, str):
        return None
    r = reason.lower()
    if r == "complete" or "every todo is done" in r:
        return "Complete"
    if "budget" in r:
        return "Role-run budget spent"
    if "board cycles" in r:
        return "Board-cycle cap reached"
    return "Other"


def stop_reason_plots(df, out: Path) -> None:
    combos = [(s, "graph_routed") for s in STRUCTURES]
    cats = ["Complete", "Role-run budget spent", "Board-cycle cap reached", "Other"]
    d = df[df.handler == "graph_routed"].assign(cat=lambda x: x.graph_stop_reason.map(stop_category))
    # average form: share of links per stop reason
    counts = pd.DataFrame({STRUCT_LABELS[s]: d[d.structure == s].cat.value_counts().reindex(cats, fill_value=0)
                           for s, _ in combos}).T
    counts = counts.loc[:, counts.sum() > 0]
    fig, ax = plt.subplots(figsize=(6.6, 3.2))
    left = np.zeros(len(counts))
    greys = dict(zip(cats, ["#4D4D4D", "#999999", "#CCCCCC", "#E8E8E8"]))
    for c in counts.columns:
        ax.barh(counts.index, counts[c], left=left, color=greys[c], edgecolor="white", label=c)
        left += counts[c].to_numpy()
    for tick, s in zip(ax.get_yticklabels(), [s for s, _ in combos]):
        tick.set_color(STRUCT_COLORS[s])
    ax.set_xlabel("Links (count)")
    ax.invert_yaxis()
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0))
    save(fig, out / "graph_stop_reason_avg_bar.png")
    # over links: stop reason per link as a dot row per structure
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    ymap = {c: i for i, c in enumerate(counts.columns)}
    for k, (s, _) in enumerate(combos):
        g = d[d.structure == s].dropna(subset=["cat"])
        ax.scatter(g.link + (k - 1) * 0.18, g.cat.map(ymap), color=STRUCT_COLORS[s], s=30, label=STRUCT_LABELS[s])
    ax.set_yticks(list(ymap.values()))
    ax.set_yticklabels(list(ymap))
    ax.set_ylim(-0.6, len(ymap) - 0.4)
    _finish_link_axis(ax)
    ax.set_xlabel("Link (iteration)")
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0))
    save(fig, out / "graph_stop_reason_line.png")


def refused_by_code_plot(df, out: Path) -> None:
    rows = []
    for (s, h), g in df.groupby(["structure", "handler"]):
        c = pd.DataFrame([json.loads(x) for x in g.refused_by_code]).fillna(0)
        for code, v in c.mean().items():
            rows.append({"structure": s, "handler": h, "code": code, "mean": v})
    t = pd.DataFrame(rows).pivot_table(index=["structure", "handler"], columns="code", values="mean", fill_value=0)
    t = t.reindex([c for c in COMBOS if c in t.index])
    t = t[t.sum().sort_values(ascending=False).index]
    pal = sns.color_palette("mako", n_colors=len(t.columns))
    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    left = np.zeros(len(t))
    labels = [HANDLER_LABELS[h] for _, h in t.index]
    for i, code in enumerate(t.columns):
        ax.barh(range(len(t)), t[code], left=left, color=pal[i], edgecolor="white", label=code)
        left += t[code].to_numpy()
    ax.set_yticks(range(len(t)))
    ax.set_yticklabels(labels)
    for tick, (s, _) in zip(ax.get_yticklabels(), t.index):
        tick.set_color(STRUCT_COLORS[s])
    ax.invert_yaxis()
    ax.set_xlabel("Refused MCP tool calls per link (mean)")
    handles = [Patch(color=STRUCT_COLORS[s], label=f"{STRUCT_LABELS[s]} (label colour)") for s in STRUCTURES]
    ax.legend(handles=ax.get_legend_handles_labels()[0] + handles, loc="upper left", bbox_to_anchor=(1.01, 1.0))
    save(fig, out / "refused_by_error_code_avg_bar.png")


def efficiency_scatter(df, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.8, 4.4))
    for s, h in COMBOS:
        g = df[(df.structure == s) & (df.handler == h)]
        ax.scatter(g.output_tokens / 1000, g.duration_s / 3600, color=STRUCT_COLORS[s], marker=HANDLER_MARKERS[h],
                   s=30, alpha=0.8, edgecolor="white", linewidth=0.4)
    ax.set_xlabel("Output tokens per link (thousands)")
    ax.set_ylabel("Link duration (h)")
    handles = [Patch(color=STRUCT_COLORS[s], label=STRUCT_LABELS[s]) for s in STRUCTURES]
    handles += [Line2D([], [], color="0.3", marker=HANDLER_MARKERS[h], linestyle="", label=HANDLER_LABELS[h])
                for h in HANDLERS]
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1.0))
    save(fig, out / "duration_vs_output_tokens_scatter.png")


def recommendation_bars(df, out: Path) -> None:
    role_cats = list(rec.ROLE_LABELS.values()) + [rec.NO_RECOMMENDATION]
    var_cats = [lab for lab, _ in rec.VARIABLE_PATTERNS] + [rec.OTHER, rec.NO_RECOMMENDATION]
    for s, h in COMBOS:
        g = df[(df.structure == s) & (df.handler == h)]
        for kind, cats, labels in (
                ("role", role_cats, [rec.role_label(r) for r in g.last_rec_to]),
                ("variable", var_cats, [lab for c in g.last_rec_change for lab in rec.variable_labels(c)])):
            counts = pd.Series(labels).value_counts().reindex(cats, fill_value=0)
            if kind == "role":
                counts = counts[(counts > 0) | (counts.index != "MDO integrator")]
            fig, ax = plt.subplots(figsize=(6.4, 0.36 * len(counts) + 0.9))
            ax.barh(range(len(counts)), counts.to_numpy(), color=STRUCT_COLORS[s], height=0.7)
            ax.set_yticks(range(len(counts)))
            ax.set_yticklabels(counts.index)
            ax.invert_yaxis()
            ax.set_xlim(0, 20)
            ax.set_xticks([0, 5, 10, 15, 20])
            ax.set_xlabel("Links whose carried-forward recommendation names it (count)" if kind == "variable"
                          else "Links recommending to this role (count)")
            save(fig, out / f"recommendations_by_{kind}_{s}_{h}.png")


# ---------------------------------------------------------------- metric catalogue

def coordination_metrics() -> list[Metric]:
    S, O, N = ("sequential",), ("orchestrated",), ("networked",)
    SP, IF, GR = ("staged_pipeline",), ("iterative_feedback",), ("graph_routed",)
    lvl = {1: "Simple", 2: "Moderate", 3: "Complex"}
    return [
        Metric("agent_steps", "agent_steps", "Agent steps per link (count)"),
        Metric("mcp_tool_calls", "kb_tool_calls", "MCP tool calls per link (count)"),
        Metric("refused_mcp_calls", "kb_refused", "Refused MCP tool calls per link (count)"),
        Metric("failed_mcp_calls", "kb_failed", "Failed MCP tool calls per link (count)"),
        Metric("verdict_complete", "verdict_complete", "COMPLETE verdicts per link (count)"),
        Metric("verdict_continue", "verdict_continue", "CONTINUE verdicts per link (count)"),
        Metric("verdict_retry", "verdict_retry", "RETRY verdicts per link (count)"),
        Metric("recommendations", "n_recommendations", "Recommendations recorded per link (count)"),
        Metric("responses", "n_responses", "Responses to recommendations per link (count)"),
        Metric("runner_attempts", "step_log_attempts", "Runner attempts per link (count)"),
        # staged pipeline
        Metric("stages_redone", "stages_redone", "Stages re-done per link (count)", handlers=SP),
        Metric("stages_not_met", "stages_not_met", "Stages not meeting completion (count)", handlers=SP),
        Metric("early_aborts", "early_abort", "Early aborts per link (count)", handlers=SP),
        # iterative feedback
        Metric("retries", "n_retry_messages", "Retried agent turns per link (count)", handlers=IF),
        Metric("stuck_stops", "stuck_detected", "Stuck-detection stops per link (count)", handlers=IF),
        Metric("attempts_to_success", "mean_attempts_to_success", "Mean attempts to success (count)", handlers=IF),
        # graph-routed
        Metric("route_length", "route_length", "Role runs on the route (count)", handlers=GR),
        Metric("backward_moves", "backward_moves", "Backward moves on the route (count)", handlers=GR,
               structures=S + O),
        Metric("complexity_level", "graph_level_num", "Complexity level reached", handlers=GR, ordinal=lvl),
        Metric("complexity_escalations", "graph_level_changes", "Complexity escalations per link (count)",
               handlers=GR),
        Metric("complexity_requests", "n_request_complexity", "request_complexity calls per link (count)",
               handlers=GR),
        Metric("board_reopenings", "graph_reopens", "Board reopenings per link (count)", handlers=GR, structures=N),
        # orchestrated
        Metric("orchestrator_steps", "orchestrator_steps", "Orchestrator steps per link (count)", structures=O),
        Metric("create_agent", "n_create_agent", "create_agent calls per link (count)", structures=O),
        Metric("assign_task", "n_assign_task", "assign_task calls per link (count)", structures=O),
        Metric("message_orchestrator", "n_message_orchestrator", "message_orchestrator calls per link (count)",
               structures=O),
        Metric("reply_to_agent", "n_reply_to_agent", "reply_to_agent calls per link (count)", structures=O),
        Metric("set_complexity", "n_set_complexity", "set_complexity calls per link (count)", structures=O,
               handlers=GR),
        # networked
        Metric("board_reads", "n_read_blackboard", "Blackboard reads per link (count)", structures=N),
        Metric("board_writes", "n_write_blackboard", "Blackboard writes per link (count)", structures=N),
        Metric("todo_reads", "n_read_todos", "TODO list reads per link (count)", structures=N),
        Metric("claims_granted", "n_claim_granted", "TODO claims granted per link (count)", structures=N),
        Metric("claims_refused", "n_claim_refused", "TODO claims refused per link (count)", structures=N),
        Metric("board_waits", "n_wait_for_board", "wait_for_board calls per link (count)", structures=N),
        Metric("todos_done", "n_mark_todo_done", "TODOs marked done per link (count)", structures=N),
        Metric("todos_failed", "n_mark_todo_failed", "TODOs marked failed per link (count)", structures=N),
        Metric("peers_spawned", "n_spawn_peer", "Peers spawned per link (count)", structures=N),
    ]


def baseline_refs(baseline: dict, df_link1_kb_old: dict) -> dict:
    refs = dict((baseline.get("get_results") or {}).get("design_parameters", {}).get("aircraft_params", {}))
    # Canonical engine inputs (config/mdo_f25_canonical_baseline.yaml -> propulsion; fan.PR is the HBTF default
    # named there). LPC/HPC PR have no recorded baseline value, so their panels get no reference line.
    refs.update({"engine.T4_MAX": 2857.0, "engine.splitter.BPR": 5.105, "engine.fan.PR": 1.685})
    refs.update(df_link1_kb_old)
    return refs


def session_defaults_from_kb(kb_path: Path, names: list[str]) -> dict:
    """old_value of each name in the first successful set_aircraft_parameters on a fresh link-1 session."""
    for line in kb_path.read_text().splitlines():
        e = json.loads(line)
        if e.get("tool") == "set_aircraft_parameters" and e.get("status") == "success":
            applied = {a["name"]: a.get("old_value") for a in (e.get("outputs") or {}).get("applied", [])}
            if all(applied.get(n) is not None for n in names):
                return {n: float(applied[n]) for n in names}
    return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--links", type=Path, required=True)
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--link1-kb", type=Path, required=True,
                    help="a link-1 knowledge_base.jsonl whose first set_aircraft_parameters shows the session defaults")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    setup_style()

    df = pd.read_csv(args.links)
    df = df[df.run_status != "in_progress"].copy()
    df["flew"] = df["flew"].astype(bool)
    df["graph_level_num"] = df["graph_level"].map({"simple": 1, "moderate": 2, "complex": 3})
    df["tokens_per_min"] = df.output_tokens / (df.duration_s / 60.0)
    baseline = json.loads(args.baseline.read_text())
    summ = baseline["run_simulation"]["summary"]
    defaults = session_defaults_from_kb(args.link1_kb, [p for p, _ in PARAMS[13:16]])
    refs = baseline_refs(baseline, defaults)
    print("baseline refs:", refs)

    base = [(summ["fuel_burned_kg"], "Baseline (unchanged design)", REF_STYLE)]
    out = args.out
    print("1 fuel")
    metric_figures(df, Metric("fuel_burn", "fuel_kg", "Fuel burn (kg)", flight=True, refs=base), out / "1_fuel")
    print("2 constraints")
    metric_figures(df, Metric("constraint_fuel_burn", "fuel_kg", "Fuel burn (kg)", flight=True,
                              refs=[(FUEL_LIMIT_KG, "Limit: fuel burn ≤ 15,000 kg", LIMIT_STYLE)]), out / "2_constraints")
    metric_figures(df, Metric("constraint_gtow", "gtow_kg", "Gross take-off weight (kg)", flight=True,
                              refs=[(GTOW_LIMIT_KG, "Limit: GTOW ≤ 90,000 kg", LIMIT_STYLE)]), out / "2_constraints")
    print("3 design parameters")
    params_average(df, refs, out / "3_design_parameters" / "params_avg_strip.png")
    for v in ("linestyle", "marker"):
        params_small_multiples(df, refs, v, out / "3_design_parameters" / f"params_line_{v}.png")
    params_heatmaps(df, refs, out / "3_design_parameters")
    print("4 coordination")
    for m in coordination_metrics():
        metric_figures(df, m, out / "4_coordination")
    stop_reason_plots(df, out / "4_coordination")
    refused_by_code_plot(df, out / "4_coordination")
    print("5 general")
    for m in (Metric("duration", "duration_s", "Link duration (h)", scale=1 / 3600),
              Metric("output_tokens", "output_tokens", "Output tokens per link (thousands)", scale=1 / 1000),
              Metric("tokens_per_minute", "tokens_per_min", "Output tokens per minute of run time")):
        metric_figures(df, m, out / "5_general")
    efficiency_scatter(df, out / "5_general")
    print("6 recommendations")
    recommendation_bars(df, out / "6_recommendations")


if __name__ == "__main__":
    main()

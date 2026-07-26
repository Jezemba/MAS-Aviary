// Results - the final outcome against the DLR-F25 reference. Metric strip,
// comparison table, per-stage breakdown with expandable DESIGN_STATE rows.

import { Fragment, useState } from "react";

import type { Prefs } from "../App";
import { AgentTile, Badge } from "../components/atoms";
import { DesignStateBlock } from "../components/CodeBlock";
import { agentById } from "../data/reference";
import type { Run } from "../data/types";
import { fmtDuration, fmtNum, fmtPct, fmtTokens, pctDelta } from "../lib/format";

type DeltaKind = "pos" | "neg" | "warn";

function Delta({ kind, children }: { kind: DeltaKind; children: React.ReactNode }) {
  return <span className={`delta ${kind}`}>{children}</span>;
}

function Metric({
  label,
  value,
  unit,
  delta,
}: {
  label: string;
  value: string;
  unit?: string;
  delta: { kind: DeltaKind; text: string };
}) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">
        {value}
        {unit && <span className="unit">{unit}</span>}
      </div>
      <div className="metric-delta">
        <Delta kind={delta.kind}>{delta.text}</Delta>
      </div>
    </div>
  );
}

export function ResultsView({ run }: { run: Run; prefs: Prefs }) {
  const { meta, result, reference, per_stage } = run;
  const [openRow, setOpenRow] = useState<string | null>(null);

  if (!result || !reference) {
    return (
      <div className="page">
        <div className="page-head">
          <h1 className="page-title">Results</h1>
        </div>
        <div className="card">
          <div className="card-body muted">No completed run yet.</div>
        </div>
      </div>
    );
  }

  const fuelDelta = pctDelta(result.fuel_burned_kg, reference.fuel_burned_kg);
  const massDelta = pctDelta(result.gross_mass_kg, reference.gross_mass_kg);
  const totalTokens = meta.total_tokens_in + meta.total_tokens_out;

  // Map iters/stages to a lookup for DESIGN_STATE expansion.
  const dstateByStage = new Map(
    run.stages
      .filter((s) => s.iters.length)
      .map((s) => [s.stage, s.iters[s.iters.length - 1].design_state]),
  );

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">
            <span className="b-dot" style={{ background: "var(--ok)", width: 8, height: 8, borderRadius: "50%", display: "inline-block" }} />
            Pipeline complete
          </h1>
          <p className="page-sub">
            <span className="mono">{meta.combo}</span> · <span className="mono">{fmtDuration(meta.elapsed_s)}</span> ·{" "}
            <span className="mono">{fmtTokens(totalTokens)} tokens</span>
          </p>
        </div>
        <div className="page-actions">
          <button className="btn btn-sm">Export CSV</button>
          <button className="btn btn-sm">Open run dir</button>
          <a className="btn btn-sm btn-primary" href={meta.wandb_url} target="_blank" rel="noreferrer">
            ↗ View on wandb
          </a>
        </div>
      </div>

      <div className="stack-14">
        {/* Metric strip */}
        <div className="metric-strip">
          <Metric
            label="Fuel burned"
            value={fmtNum(result.fuel_burned_kg)}
            unit="kg"
            delta={{ kind: "warn", text: `${fmtPct(fuelDelta)} vs F25` }}
          />
          <Metric
            label="Gross mass"
            value={fmtNum(result.gross_mass_kg)}
            unit="kg"
            delta={{ kind: "pos", text: `${fmtPct(massDelta)} vs F25` }}
          />
          <Metric
            label="L/D cruise"
            value={fmtNum(result.ld_cruise, 1)}
            delta={{ kind: result.ld_fallback ? "warn" : "pos", text: result.ld_fallback ? "fallback" : "converged" }}
          />
          <Metric
            label="Converged"
            value={result.converged ? "Yes" : "No"}
            delta={{ kind: result.converged ? "pos" : "neg", text: `gap ${result.optimality_gap_pct.toFixed(1)}%` }}
          />
        </div>

        {/* Comparison table */}
        <div className="card">
          <div className="card-head">
            <h3 className="card-title">Result vs DLR-F25 reference</h3>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th className="num">This run</th>
                  <th className="num">F25 reference</th>
                  <th className="num">Δ</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>FUEL_BURNED_KG</td>
                  <td className="num">{fmtNum(result.fuel_burned_kg)}</td>
                  <td className="num">{fmtNum(reference.fuel_burned_kg)}</td>
                  <td className="num"><Delta kind="warn">{fmtPct(fuelDelta)}</Delta></td>
                </tr>
                <tr>
                  <td>GROSS_MASS_KG</td>
                  <td className="num">{fmtNum(result.gross_mass_kg)}</td>
                  <td className="num">{fmtNum(reference.gross_mass_kg)}</td>
                  <td className="num"><Delta kind="pos">{fmtPct(massDelta)}</Delta></td>
                </tr>
                <tr>
                  <td>
                    L/D (cruise)
                    {result.ld_fallback && <span className="cell-note"> · fallback (SU2 underconverged)</span>}
                  </td>
                  <td className="num">{fmtNum(result.ld_cruise, 1)}</td>
                  <td className="num">{fmtNum(reference.ld_cruise, 1)}</td>
                  <td className="num">-</td>
                </tr>
                <tr>
                  <td>CONVERGED</td>
                  <td className="num">{String(result.converged)}</td>
                  <td className="num">-</td>
                  <td className="num">-</td>
                </tr>
                <tr>
                  <td>CONSTRAINTS_FAILED</td>
                  <td className="num">{result.constraints_failed.length ? result.constraints_failed.join(", ") : "none"}</td>
                  <td className="num">-</td>
                  <td className="num">-</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        {/* Per-stage breakdown */}
        <div className="card">
          <div className="card-head">
            <div>
              <h3 className="card-title">Per-stage breakdown</h3>
              <p className="card-sub">Click any row to inspect the agent's final DESIGN_STATE</p>
            </div>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Stage</th>
                  <th>MCP</th>
                  <th>Status</th>
                  <th className="num">Iters</th>
                  <th className="num">Tokens</th>
                  <th className="num">Duration</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {per_stage?.map((row) => {
                  const a = agentById(row.stage)!;
                  const ds = dstateByStage.get(row.stage);
                  const isOpen = openRow === row.stage;
                  return (
                    <Fragment key={row.stage}>
                      <tr
                        onClick={() => setOpenRow(isOpen ? null : row.stage)}
                        style={{ cursor: ds ? "pointer" : "default" }}
                      >
                        <td>
                          <div className="row" style={{ gap: 8 }}>
                            <AgentTile emoji={a.emoji} size="sm" />
                            <div>
                              <div style={{ fontWeight: 500 }}>{a.name}</div>
                              {row.note && <div className="cell-note">{row.note}</div>}
                            </div>
                          </div>
                        </td>
                        <td className="mono" style={{ fontSize: 12 }}>{a.mcp}</td>
                        <td><Badge status={row.status} /></td>
                        <td className="num">{row.iters > 1 ? <span className="iters-strong">{row.iters}×</span> : row.iters}</td>
                        <td className="num">{fmtTokens(row.tokens)}</td>
                        <td className="num">{fmtDuration(row.duration_s)}</td>
                        <td className="num" style={{ color: "var(--ink-4)" }}>{ds ? (isOpen ? "▴" : "▾") : ""}</td>
                      </tr>
                      {isOpen && ds && (
                        <tr>
                          <td colSpan={7} style={{ background: "var(--surface-2)" }}>
                            <DesignStateBlock data={ds} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

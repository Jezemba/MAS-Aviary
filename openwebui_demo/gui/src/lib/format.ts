// Formatting helpers - ported verbatim from the design prototype's data.jsx
// (the README calls these out as "small and worth porting verbatim").

export function fmtDuration(s: number | null | undefined): string {
  if (s == null) return "-";
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return `${m}m ${String(r).padStart(2, "0")}s`;
}

export function fmtTokens(n: number | null | undefined): string {
  if (n == null) return "-";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(n >= 10e3 ? 0 : 1) + "k";
  return String(n);
}

export function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n == null || isNaN(n)) return "-";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function pctDelta(actual: number, ref: number): number {
  if (!ref) return 0;
  return ((actual - ref) / ref) * 100;
}

// mm:ss position readout for the replay transport bar.
export function fmtClock(s: number): string {
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

// Signed percent string, e.g. +5.3% / -13.9%.
export function fmtPct(p: number, digits = 1): string {
  const sign = p > 0 ? "+" : "";
  return `${sign}${p.toFixed(digits)}%`;
}

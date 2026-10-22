// Small SVG charts (no chart library).
import type { TrajPhase } from "./types";

export interface Series {
  values: (number | null)[]; // index = link - 1
}

/** Value per link with the N-1 .. N+1 window shaded and the current link marked. */
export function LinkChart({ values, current, height = 54 }: { values: (number | null)[]; current: number; height?: number }) {
  const w = 220;
  const pad = 4;
  const nums = values.filter((v): v is number => v !== null && Number.isFinite(v));
  if (!nums.length) return <div className="chart-empty">no values in the files</div>;
  let lo = Math.min(...nums);
  let hi = Math.max(...nums);
  if (hi === lo) {
    hi += 1;
    lo -= 1;
  }
  const n = Math.max(values.length, 2);
  const x = (i: number) => pad + (i * (w - 2 * pad)) / (n - 1);
  const y = (v: number) => height - pad - ((v - lo) * (height - 2 * pad)) / (hi - lo);
  // break the line where a link has no value
  const segs: string[] = [];
  let cur = "";
  values.forEach((v, i) => {
    if (v === null || !Number.isFinite(v)) {
      if (cur) segs.push(cur);
      cur = "";
    } else cur += `${cur ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
  });
  if (cur) segs.push(cur);
  const ci = current - 1;
  const winLo = Math.max(ci - 1, 0);
  const winHi = Math.min(ci + 1, values.length - 1);
  return (
    <svg className="chart" viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" role="img">
      <rect x={x(winLo) - 3} y={0} width={Math.max(x(winHi) - x(winLo) + 6, 6)} height={height} className="chart-window" />
      {segs.map((d, k) => <path key={k} d={d} className="chart-line" />)}
      {values.map((v, i) =>
        v === null || !Number.isFinite(v) ? (
          <line key={i} x1={x(i)} x2={x(i)} y1={height - 6} y2={height - 1} className="chart-missing" />
        ) : (
          <circle key={i} cx={x(i)} cy={y(v)} r={i === ci ? 3.2 : 1.6} className={i === ci ? "chart-dot-cur" : "chart-dot"} />
        ),
      )}
    </svg>
  );
}

/** Running value within the link, up to the cursor. */
export function Spark({ values }: { values: (number | null)[] }) {
  const w = 220;
  const h = 26;
  const nums = values.filter((v): v is number => v !== null && Number.isFinite(v));
  if (nums.length < 2) return null;
  const lo = Math.min(...nums);
  const hi = Math.max(...nums) === lo ? lo + 1 : Math.max(...nums);
  let pen = false; // start a new segment after a gap
  const d = values
    .map((v, i) => {
      if (v === null || !Number.isFinite(v)) {
        pen = false;
        return "";
      }
      const cmd = pen ? "L" : "M";
      pen = true;
      return `${cmd}${((i * w) / (values.length - 1)).toFixed(1)},${(h - 2 - ((v - lo) * (h - 4)) / (hi - lo)).toFixed(1)}`;
    })
    .join("");
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <path d={d} className="spark-line" />
    </svg>
  );
}

const TRAJ_Y: Record<string, { key: keyof TrajPhase; label: string }> = {
  altitude: { key: "altitude_ft", label: "Altitude [ft]" },
  mach: { key: "mach", label: "Mach" },
  mass: { key: "mass_kg", label: "Mass [kg]" },
  throttle: { key: "throttle", label: "Throttle" },
};

function flatten(phases: TrajPhase[] | undefined, yKey: keyof TrajPhase): [number, number][] {
  const pts: [number, number][] = [];
  (phases ?? []).forEach((p) => {
    const xs = p.distance_nmi ?? [];
    const ys = (p[yKey] as number[] | undefined) ?? [];
    xs.forEach((xv, i) => {
      if (ys[i] !== undefined) pts.push([xv, ys[i]]);
    });
  });
  return pts;
}

/** Mission flown: link N (solid) over link N-1 (dashed), against distance. */
export function TrajectoryChart({ now, prev, yName }: { now?: TrajPhase[]; prev?: TrajPhase[]; yName: string }) {
  const y = TRAJ_Y[yName] ?? TRAJ_Y.altitude;
  const a = flatten(now, y.key);
  const b = flatten(prev, y.key);
  const all = [...a, ...b];
  if (!all.length) return <div className="chart-empty">no trajectory for this link or the previous one</div>;
  const w = 360;
  const h = 210;
  const L = 52;
  const B = 26;
  const xs = all.map((p) => p[0]);
  const ys = all.map((p) => p[1]);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs) || 1;
  let y0 = Math.min(...ys);
  let y1 = Math.max(...ys);
  if (y0 === y1) {
    y0 -= 1;
    y1 += 1;
  }
  const px = (v: number) => L + ((v - x0) * (w - L - 8)) / (x1 - x0 || 1);
  const py = (v: number) => h - B - ((v - y0) * (h - B - 10)) / (y1 - y0);
  const path = (pts: [number, number][]) => pts.map((p, i) => `${i ? "L" : "M"}${px(p[0]).toFixed(1)},${py(p[1]).toFixed(1)}`).join("");
  const ticks = [0, 0.5, 1].map((f) => y0 + f * (y1 - y0));
  return (
    <svg className="traj" viewBox={`0 0 ${w} ${h}`}>
      <line x1={L} y1={h - B} x2={w - 8} y2={h - B} className="axis" />
      <line x1={L} y1={10} x2={L} y2={h - B} className="axis" />
      {ticks.map((t, k) => (
        <text key={k} x={L - 4} y={py(t) + 3} className="tick" textAnchor="end">
          {Math.abs(t) >= 1000 ? Math.round(t).toLocaleString() : t.toFixed(2)}
        </text>
      ))}
      <text x={L} y={h - 8} className="tick">{x0.toFixed(0)}</text>
      <text x={w - 8} y={h - 8} className="tick" textAnchor="end">{x1.toFixed(0)} nmi</text>
      <text x={12} y={h / 2} className="tick" transform={`rotate(-90 12 ${h / 2})`} textAnchor="middle">{y.label}</text>
      {b.length > 0 && <path d={path(b)} className="traj-prev" />}
      {a.length > 0 && <path d={path(a)} className="traj-now" />}
    </svg>
  );
}

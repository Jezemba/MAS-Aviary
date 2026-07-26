// Shared atoms: Badge, StatusGlyph, AgentTile, Spinner. Everything else
// depends on these (handoff build order step 2).

import type { Status, ToolStatus } from "../data/types";

export function Spinner() {
  return <span className="spinner" aria-label="running" />;
}

const STATUS_LABEL: Record<Status, string> = {
  pending: "pending",
  running: "running",
  success: "success",
  partial: "partial",
  failed: "failed",
};

const STATUS_CLASS: Record<Status, string> = {
  pending: "is-pending",
  running: "is-running",
  success: "is-success",
  partial: "is-partial",
  failed: "is-failed",
};

// Pill badge with a currentColor dot. Running shows a spinner instead of a dot.
export function Badge({ status, label }: { status: Status; label?: string }) {
  return (
    <span className={`badge ${STATUS_CLASS[status]}`}>
      {status === "running" ? <Spinner /> : <span className="b-dot" />}
      {label ?? STATUS_LABEL[status]}
    </span>
  );
}

// Small glyph used in the tool-log rows: spinner / ✓ / ! / ✕ / ·
export function StatusGlyph({ status }: { status: ToolStatus }) {
  if (status === "running") return <Spinner />;
  const map: Record<Exclude<ToolStatus, "running">, { ch: string; color: string; title: string }> = {
    success: { ch: "✓", color: "var(--ok)", title: "success" },
    error:   { ch: "✕", color: "var(--err)", title: "error" },
    retry:   { ch: "!", color: "var(--warn)", title: "retry" },
    pending: { ch: "·", color: "var(--ink-4)", title: "pending" },
  };
  const g = map[status];
  return (
    <span style={{ color: g.color }} title={g.title}>
      {g.ch}
    </span>
  );
}

// Neutral tile holding an agent emoji. `size` toggles the smaller pending-strip
// variant. In the progress strip the "done" bubble variant is handled inline.
export function AgentTile({
  emoji,
  size = "md",
}: {
  emoji: string;
  size?: "sm" | "md" | "lg";
}) {
  const px = size === "sm" ? 22 : size === "lg" ? 30 : 28;
  const radius = size === "sm" ? 5 : 7;
  const font = size === "sm" ? 12 : 14;
  return (
    <span
      className="agent-emoji"
      style={{ width: px, height: px, borderRadius: radius, fontSize: font }}
      aria-hidden
    >
      {emoji}
    </span>
  );
}

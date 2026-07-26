// Setup - configure and launch a run. Ported from the prototype's SetupView,
// wired to the App's setup state. v1 only allows sequential × iterative_feedback.

import type { SetupState } from "../App";
import { HANDLERS, MISSION_PRESETS, STRUCTURES, comboName, isValidCombo } from "../data/reference";

function Field({ label, hint, value }: { label: string; hint: string; value: string }) {
  return (
    <div className="field">
      <label className="field-label">
        {label}
        <span className="field-hint">{hint}</span>
      </label>
      <input className="input mono" value={value} readOnly />
    </div>
  );
}

function NumField({
  label,
  unit,
  value,
  onChange,
  readOnly = false,
}: {
  label: string;
  unit: string;
  value: number;
  onChange?: (v: number) => void;
  readOnly?: boolean;
}) {
  return (
    <div className="field">
      <label className="field-label">
        {label}
        <span className="field-hint">{unit}</span>
      </label>
      <input
        className="input mono"
        value={value}
        readOnly={readOnly}
        style={readOnly ? { color: "var(--ink-3)", background: "var(--bg)" } : undefined}
        onChange={(e) => {
          if (readOnly || !onChange) return;
          const v = parseFloat(e.target.value);
          if (!Number.isNaN(v)) onChange(v);
        }}
      />
    </div>
  );
}

export function SetupView({
  setup,
  onChange,
  onLaunch,
}: {
  setup: SetupState;
  onChange: (s: SetupState) => void;
  onLaunch: () => void;
}) {
  const set = (patch: Partial<SetupState>) => onChange({ ...setup, ...patch });
  const cellReady = isValidCombo(setup.structure, setup.handler);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Configure run</h1>
          <p className="page-sub">
            Pick an org structure × handler, then a mission profile. Defaults are pre-set for the
            DLR-F25 benchmark.
          </p>
        </div>
        <button className="btn btn-sm btn-ghost" disabled>
          ⌥ Open YAML
        </button>
      </div>

      {/* Card A - structure × handler + coverage matrix */}
      <div className="card">
        <div className="card-head">
          <div>
            <h3 className="card-title">Organizational structure × handler</h3>
            <p className="card-sub">
              All 8 valid combinations run end-to-end.{" "}
              <span className="mono">networked × staged_pipeline</span> is not wired for MDO-F25.
            </p>
          </div>
          {!cellReady && (
            <span className="badge is-partial">
              <span className="b-dot" /> not a valid combination
            </span>
          )}
        </div>
        <div className="card-body">
          <div className="field-row">
            <div className="field">
              <label className="field-label">
                Organizational structure
                <span className="field-hint">how agents are wired</span>
              </label>
              <select
                className="select"
                value={setup.structure}
                onChange={(e) => set({ structure: e.target.value })}
              >
                {STRUCTURES.map((s) => (
                  <option key={s.id} value={s.id} disabled={!s.available}>
                    {s.label}
                    {!s.available ? " - coming soon" : ""}
                  </option>
                ))}
              </select>
              <p className="field-desc">{STRUCTURES.find((s) => s.id === setup.structure)?.desc}</p>
            </div>
            <div className="field">
              <label className="field-label">
                Handler
                <span className="field-hint">how output is judged</span>
              </label>
              <select
                className="select"
                value={setup.handler}
                onChange={(e) => set({ handler: e.target.value })}
              >
                {HANDLERS.map((h) => (
                  <option key={h.id} value={h.id} disabled={!h.available}>
                    {h.label}
                    {!h.available ? " - coming soon" : ""}
                  </option>
                ))}
              </select>
              <p className="field-desc">{HANDLERS.find((h) => h.id === setup.handler)?.desc}</p>
            </div>
          </div>

          <div className="divider" />

          <div className="field-label" style={{ marginBottom: 10 }}>
            <span>Coverage matrix</span>
            <span className="field-hint">click to select</span>
          </div>
          <div className="matrix">
            <div className="matrix-corner" />
            {HANDLERS.map((h) => (
              <div key={h.id} className="matrix-colhead">
                {h.label}
              </div>
            ))}
            {STRUCTURES.map((s) => (
              <FragmentRow key={s.id} sid={s.id} label={s.label} setup={setup} onSelect={set} />
            ))}
          </div>
        </div>
      </div>

      {/* Card B - mission profile */}
      <div className="card">
        <div className="card-head">
          <div>
            <h3 className="card-title">Mission profile</h3>
            <p className="card-sub">Aircraft baseline, range, payload, and the initial design point.</p>
          </div>
        </div>
        <div className="card-body">
          <div className="field" style={{ marginBottom: 18 }}>
            <label className="field-label">Preset</label>
            <div className="chips">
              {MISSION_PRESETS.map((m) => (
                <button
                  key={m.id}
                  className={`chip ${setup.missionPreset === m.id ? "is-active" : ""}`}
                  onClick={() => set({ missionPreset: m.id })}
                >
                  {m.label} <span style={{ opacity: 0.6, fontSize: 11 }}>· {m.desc}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="field-row" style={{ gridTemplateColumns: "1fr 1fr 1fr 1fr" }}>
            <Field label="Range" hint="nmi" value="2 500" />
            <Field label="Payload" hint="pax" value="200" />
            <Field label="Cruise" hint="Mach" value="0.78" />
            <Field label="Altitude" hint="ft" value="33 000" />
          </div>

          <div className="divider" />

          <div className="field-label" style={{ marginBottom: 10 }}>
            <span>Initial design point</span>
            <span className="field-hint">the seed samples this on live runs</span>
          </div>
          <div className="field-row" style={{ gridTemplateColumns: "repeat(3, 1fr) 80px" }}>
            <NumField label="AREA" unit="m² · seeded" value={setup.initialParams.AREA} readOnly />
            <NumField label="ASPECT_RATIO" unit="seeded" value={setup.initialParams.ASPECT_RATIO} readOnly />
            <NumField label="SCALE_FACTOR" unit="seeded" value={setup.initialParams.SCALE_FACTOR} readOnly />
            <NumField label="Seed" unit="drives ↑" value={setup.seed} onChange={(v) => set({ seed: v })} />
          </div>
          <p className="field-desc" style={{ marginTop: 8 }}>
            On a live run the design point is sampled from the seed (not typed here) — change
            the <span className="mono">Seed</span> to get a different geometry/mass/fuel outcome.
          </p>
        </div>
      </div>

      {/* Card C - runtime */}
      <div className="card">
        <div className="card-head">
          <div>
            <h3 className="card-title">Runtime</h3>
            <p className="card-sub">Repeats and timeout drive live runs; model is set in the config YAML. Per-repeat cost ≈ $0.30 · 10-15 min.</p>
          </div>
        </div>
        <div className="card-body">
          <div className="field-row" style={{ gridTemplateColumns: "2fr 1fr 1fr" }}>
            <div className="field">
              <label className="field-label">
                Model <span className="field-hint">from config YAML</span>
              </label>
              <select className="select" value={setup.model} disabled title="Live runs use the model in config/mdo_f25_run_claude.yaml; not settable from the GUI yet.">
                <option value="anthropic/claude-sonnet-4-20250514">anthropic / claude-sonnet-4-20250514</option>
                <option value="anthropic/claude-opus-4-20250514">anthropic / claude-opus-4-20250514</option>
                <option value="openai/gpt-4.1-2025-04-14">openai / gpt-4.1-2025-04-14</option>
              </select>
            </div>
            <NumField label="Repeats" unit="× runs" value={setup.repeats} onChange={(v) => set({ repeats: v })} />
            <NumField label="Timeout per repeat" unit="min" value={setup.timeoutMin} onChange={(v) => set({ timeoutMin: v })} />
          </div>
        </div>
      </div>

      {/* Card D - launch bar */}
      <div className="card" style={{ marginTop: 18, background: "var(--surface-2)" }}>
        <div className="card-body">
          <div className="spread">
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>Ready to launch</div>
              <div className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }}>
                combo_name: {comboName(setup.structure, setup.handler)}
              </div>
            </div>
            <div className="row" style={{ gap: 8 }}>
              <button className="btn">Save as YAML</button>
              <button className="btn btn-primary" onClick={onLaunch} disabled={!cellReady}>
                Launch run →
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// One matrix row: the row header + four cells.
function FragmentRow({
  sid,
  label,
  setup,
  onSelect,
}: {
  sid: string;
  label: string;
  setup: SetupState;
  onSelect: (patch: Partial<SetupState>) => void;
}) {
  return (
    <>
      <div className="matrix-rowhead">{label}</div>
      {HANDLERS.map((h) => {
        const ready = isValidCombo(sid, h.id);
        const selected = sid === setup.structure && h.id === setup.handler;
        return (
          <button
            key={h.id}
            className={`matrix-cell ${ready ? "is-ready" : "is-disabled"} ${selected ? "is-selected" : ""}`}
            disabled={!ready}
            onClick={() => ready && onSelect({ structure: sid, handler: h.id })}
            title={ready ? comboName(sid, h.id) : "networked_staged_pipeline is not wired for MDO-F25"}
          >
            {ready ? "✓ ready" : "n/a"}
          </button>
        );
      })}
    </>
  );
}

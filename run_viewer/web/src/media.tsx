// Image pair (N-1 | N) with a hover preview and a click-to-enlarge lightbox.
import { useEffect, useState, type ReactNode } from "react";

export interface Shot {
  key: string;
  title: string;
  subtitle?: ReactNode;
  src: string | null; // null = no image; `missing` says why
  missing?: string;
}

function Missing({ text }: { text: string }) {
  return (
    <div className="shot-missing">
      <span>{text}</span>
    </div>
  );
}

export function ShotPair({ group, shots }: { group: string; shots: Shot[] }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <>
      <div className="shot-pair">
        {shots.map((s, k) => (
          <figure key={s.key} className="shot">
            <figcaption>
              <span className="shot-title">{s.title}</span>
              {s.subtitle && <span className="shot-sub">{s.subtitle}</span>}
            </figcaption>
            {s.src ? (
              <button className="shot-btn" onClick={() => setOpen(k)} aria-label={`Enlarge ${group}: ${s.title}`}>
                <img src={s.src} alt={`${group}: ${s.title}`} loading="lazy" />
                <span className="shot-zoom" aria-hidden>⤢</span>
                <span className="shot-hover" aria-hidden>
                  <img src={s.src} alt="" />
                </span>
              </button>
            ) : (
              <Missing text={s.missing ?? "no image"} />
            )}
          </figure>
        ))}
      </div>
      {open !== null && <Lightbox group={group} shots={shots} start={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function Lightbox({ group, shots, start, onClose }: { group: string; shots: Shot[]; start: number; onClose: () => void }) {
  const [focus, setFocus] = useState(start);
  const [both, setBoth] = useState(true);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft") setFocus((f) => Math.max(0, f - 1));
      if (e.key === "ArrowRight") setFocus((f) => Math.min(shots.length - 1, f + 1));
      e.stopPropagation();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose, shots.length]);
  const shown = both ? shots : [shots[focus]];
  return (
    <div className="lightbox" role="dialog" aria-modal="true" aria-label={group} onClick={onClose}>
      <div className="lightbox-card" onClick={(e) => e.stopPropagation()}>
        <header>
          <h3>{group}</h3>
          <div className="lightbox-tools">
            <button className={both ? "on" : ""} onClick={() => setBoth(true)}>Side by side</button>
            {shots.map((s, k) => (
              <button key={s.key} className={!both && focus === k ? "on" : ""} onClick={() => { setBoth(false); setFocus(k); }}>
                {s.title}
              </button>
            ))}
            <button className="close" onClick={onClose} aria-label="Close">✕</button>
          </div>
        </header>
        <div className={`lightbox-body n${shown.length}`}>
          {shown.map((s) => (
            <figure key={s.key}>
              <figcaption><b>{s.title}</b> {s.subtitle}</figcaption>
              {s.src ? <img src={s.src} alt={`${group}: ${s.title}`} /> : <Missing text={s.missing ?? "no image"} />}
            </figure>
          ))}
        </div>
        <footer className="muted small">Esc to close · ← → to switch when viewing one image</footer>
      </div>
    </div>
  );
}

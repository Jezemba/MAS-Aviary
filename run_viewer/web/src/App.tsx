import { useCallback, useEffect, useMemo, useState } from "react";
import { STATIC, urls } from "./api";
import { Communication, CurrentAgent, Images, Metrics, Params, Recommendation, Strip } from "./panels";
import { currentTurnIndex } from "./playback";
import type { CatalogEntry, Link, Run } from "./types";

const SPEEDS = [0.5, 1, 2, 4, 8];
const LIVE_POLL_MS = 10000;

async function getJson<T>(url: string): Promise<T | null> {
  const r = await fetch(url, { cache: "no-store" });
  return r.ok ? ((await r.json()) as T) : null;
}

// The link asked for in the URL, read once (the run loader rewrites the URL).
const INITIAL_LINK = Number(new URLSearchParams(location.search).get("link")) || 1;

const linkUrl = urls.link;

/** The run dropdowns: inputs that a future run would take (combination, model, links...). */
function useCatalogFilters(catalog: CatalogEntry[]) {
  const [f, setF] = useState<Record<string, string>>({});
  const key = (e: CatalogEntry) => ({
    combination: e.title ?? e.combination,
    model: e.inputs?.model ?? "—",
    links: String(e.inputs?.links_planned ?? "—"),
    thinking: e.inputs?.thinking === null || e.inputs?.thinking === undefined ? "—" : e.inputs.thinking ? "on" : "off",
    study: e.label ?? e.run_id,
  });
  const options = (name: string) => [...new Set(catalog.map((e) => (key(e) as Record<string, string>)[name]))];
  const matches = catalog.filter((e) => Object.entries(f).every(([k, v]) => !v || (key(e) as Record<string, string>)[k] === v));
  return { f, setF, options, matches, key };
}

export default function App() {
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [linkNo, setLinkNo] = useState(1);
  const [link, setLink] = useState<Link | null>(null);
  const [prev, setPrev] = useState<Link | null>(null);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [follow, setFollow] = useState(true);
  const filters = useCatalogFilters(catalog);

  useEffect(() => {
    getJson<CatalogEntry[]>(urls.catalog()).then((c) => {
      if (!c) return;
      setCatalog(c);
      const fromUrl = new URLSearchParams(location.search).get("run");
      setRunId(c.find((e) => e.run_id === fromUrl)?.run_id ?? c.find((e) => e.built)?.run_id ?? null);
    });
  }, []);

  // run.json; live runs are polled
  useEffect(() => {
    if (!runId) return;
    let stop = false;
    const load = () => getJson<Run>(urls.run(runId)).then((r) => !stop && r && setRun(r));
    setRun(null);
    const first = new URLSearchParams(location.search).get("run") === runId;
    load().then(() => setLinkNo(first ? INITIAL_LINK : 1));
    const live = catalog.find((e) => e.run_id === runId)?.live;
    const id = live ? setInterval(load, LIVE_POLL_MS) : undefined;
    return () => {
      stop = true;
      if (id) clearInterval(id);
    };
  }, [runId, catalog]);

  // the link (and N-1 for the comparison images); live link re-fetched when run.json changes
  const runStamp = run?.built_utc;
  useEffect(() => {
    if (!run) return;
    let stop = false;
    getJson<Link>(linkUrl(run.run_id, linkNo)).then((l) => {
      if (stop) return;
      setLink((old) => {
        const same = old && old.link === l?.link;
        if (!same) setCursor(0);
        if (same && run.live && follow && l) setCursor(l.steps.length - 1);
        return l;
      });
    });
    if (linkNo > 1) getJson<Link>(linkUrl(run.run_id, linkNo - 1)).then((l) => !stop && setPrev(l));
    else setPrev(null);
    history.replaceState(null, "", `?run=${run.run_id}&link=${linkNo}`);
    return () => {
      stop = true;
    };
  }, [run?.run_id, linkNo, runStamp]); // eslint-disable-line react-hooks/exhaustive-deps

  const nLinks = run?.links.length ?? 0;
  const nSteps = link?.steps.length ?? 0;
  const goLink = useCallback((n: number) => setLinkNo(Math.min(Math.max(n, 1), Math.max(nLinks, 1))), [nLinks]);

  // play: one step per (1 / speed) seconds; at the end of a finished link go on to the next
  useEffect(() => {
    if (!playing || !link) return;
    const id = setTimeout(() => {
      if (cursor < nSteps - 1) setCursor(cursor + 1);
      else if (link.complete && linkNo < nLinks) goLink(linkNo + 1);
      else setPlaying(false);
    }, 1000 / speed);
    return () => clearTimeout(id);
  }, [playing, cursor, nSteps, link, linkNo, nLinks, speed, goLink]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "SELECT" || (e.target as HTMLElement).tagName === "INPUT") return;
      if (e.key === " ") { e.preventDefault(); setPlaying((p) => !p); }
      if (e.key === "ArrowRight") setCursor((c) => Math.min(c + 1, nSteps - 1));
      if (e.key === "ArrowLeft") setCursor((c) => Math.max(c - 1, 0));
      if (e.key === "PageDown" || e.key === "]") goLink(linkNo + 1);
      if (e.key === "PageUp" || e.key === "[") goLink(linkNo - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [nSteps, linkNo, goLink]);

  const entry = catalog.find((e) => e.run_id === runId);
  const currentTurn = useMemo(() => (link ? currentTurnIndex(link.turns, link.steps[cursor]) : -1), [link, cursor]);
  const hasComm = run && run.org !== "sequential";

  return (
    <div className={`app org-${run?.org ?? "none"}`}>
      <header className="topbar">
        <div className="title">
          <span className="brand">MAS-Aviary · run viewer{STATIC ? " · static snapshot" : ""}</span>
          <div className="title-row">
            <h1>{run?.title ?? "Loading…"}</h1>
            {run && <span className="badge">{run.label}</span>}
            {entry?.live && <span className="live"><span className="dot" />LIVE</span>}
          </div>
          {entry?.note && <span className="note">⚠ {entry.note}</span>}
        </div>
        <div className="inputs">
          {(["study", "combination", "model", "links", "thinking"] as const).map((name) => (
            <label key={name} className="field">
              <span>{name === "links" ? "links in run" : name}</span>
              <select
                value={entry ? (filters.key(entry) as Record<string, string>)[name] : ""}
                onChange={(e) => {
                  const next = { ...filters.f, [name]: e.target.value };
                  filters.setF(next);
                  const hit = catalog.find((c) => Object.entries(next).every(([k, v]) => !v || (filters.key(c) as Record<string, string>)[k] === v));
                  if (hit) setRunId(hit.run_id);
                }}
              >
                {filters.options(name).map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            </label>
          ))}
        </div>
      </header>

      {run && link && (
        <div className="controls">
          <button onClick={() => goLink(linkNo - 1)} disabled={linkNo <= 1} title="previous link ([)">⏮ link</button>
          <button onClick={() => setCursor(Math.max(cursor - 1, 0))} disabled={cursor <= 0} title="step back (←)">◀ step</button>
          <button className="play" onClick={() => setPlaying(!playing)} title="play / pause (space)">{playing ? "⏸ pause" : "▶ play"}</button>
          <button onClick={() => setCursor(Math.min(cursor + 1, nSteps - 1))} disabled={cursor >= nSteps - 1} title="step forward (→)">step ▶</button>
          <button onClick={() => goLink(linkNo + 1)} disabled={linkNo >= nLinks} title="next link (])">link ⏭</button>
          <label className="field inline">
            <span>link</span>
            <select value={linkNo} onChange={(e) => goLink(Number(e.target.value))}>
              {run.links.map((l) => (
                <option key={l.link} value={l.link}>
                  {l.link} {l.complete ? (l.flown === false ? "· did not fly" : "") : "· running"}
                </option>
              ))}
            </select>
            <span className="muted">of {run.inputs.links_planned ?? "?"}</span>
          </label>
          <input type="range" min={0} max={Math.max(nSteps - 1, 0)} value={cursor} onChange={(e) => setCursor(Number(e.target.value))} />
          <span className="muted">step {nSteps ? cursor + 1 : 0}/{nSteps}</span>
          <label className="field inline">
            <span>speed</span>
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
              {SPEEDS.map((s) => <option key={s} value={s}>{s} step/s</option>)}
            </select>
          </label>
          {run.live && (
            <label className="field inline"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow live</label>
          )}
          {link.attempt > 1 && <span className="muted">attempt {link.attempt} (last attempt is the link's result)</span>}
        </div>
      )}

      {!run && <p className="muted pad">{catalog.length ? "Loading run…" : "Loading catalog…"}</p>}
      {run && !link && <p className="muted pad">This link has no bundle yet.</p>}

      {run && link && (
        <div className={`shell ${hasComm ? "with-comm" : ""}`}>
          <main className="main">
            <div className="a-agent"><CurrentAgent run={run} link={link} cursor={cursor} /></div>
            <div className="a-images"><Images run={run} link={link} prev={prev} cursor={cursor} /></div>
            <div className="a-pn"><Params link={link} side="N" /></div>
            <div className="a-pn1"><Params link={link} side="N+1" /></div>
            <div className="a-rec"><Recommendation link={link} cursor={cursor} /></div>
            <div className="a-strip"><Strip run={run} link={link} cursor={cursor} currentTurn={currentTurn} /></div>
          </main>
          <aside className="side">
            <Metrics run={run} link={link} cursor={cursor} />
            {hasComm && <Communication run={run} link={link} cursor={cursor} />}
          </aside>
        </div>
      )}
    </div>
  );
}

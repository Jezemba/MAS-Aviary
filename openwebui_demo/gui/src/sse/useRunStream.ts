// Consume the chat_server structured Run-snapshot SSE stream. Both live and
// replay flow through the SAME endpoint (only ?mode= and pacing differ), so the
// GUI has one code path for both - as the design brief requires.
//
// The server reduces events → Run snapshots (reusing narration.py), so this
// hook is deliberately thin: parse `data:` frames, setRun(snapshot), track phase.

import { useEffect, useRef, useState } from "react";

import type { Run, RunPhase } from "../data/types";

// chat_server.py default. Override with VITE_RUN_STREAM_BASE for a remote host.
const BASE = import.meta.env.VITE_RUN_STREAM_BASE ?? "http://127.0.0.1:8090";

export interface StreamOpts {
  mode: "replay" | "live";
  speed?: number;
  combo?: string;
  structure?: string;
  handler?: string;
  // Live-run parameters that map to real runner CLI flags.
  repeats?: number;
  timeoutMin?: number;
  seed?: number;
  // Sent as the X-Live-Token header for mode=live (never placed in the URL).
  liveToken?: string;
  enabled?: boolean;
  // Bump to restart the stream (e.g. Replay "Restart").
  epoch?: number;
}

export interface StreamState {
  run: Run | null;
  phase: RunPhase;
  error: string | null;
}

function buildUrl(o: StreamOpts): string {
  const p = new URLSearchParams({ mode: o.mode });
  if (o.speed != null) p.set("speed", String(o.speed));
  if (o.combo) p.set("combo", o.combo);
  if (o.structure) p.set("structure", o.structure);
  if (o.handler) p.set("handler", o.handler);
  if (o.repeats != null) p.set("repeats", String(o.repeats));
  if (o.timeoutMin != null) p.set("timeout", String(o.timeoutMin));
  if (o.seed != null) p.set("seed", String(o.seed));
  return `${BASE}/v1/runs/stream?${p.toString()}`;
}

export function useRunStream(opts: StreamOpts): StreamState {
  const { mode, speed, combo, structure, handler, repeats, timeoutMin, seed, liveToken, enabled = true, epoch = 0 } = opts;
  const [run, setRun] = useState<Run | null>(null);
  const [phase, setPhase] = useState<RunPhase>("idle");
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!enabled) return;
    const ac = new AbortController();
    abortRef.current = ac;
    setPhase("running");
    setError(null);

    (async () => {
      try {
        const headers: Record<string, string> = { Accept: "text/event-stream" };
        if (mode === "live" && liveToken) headers["X-Live-Token"] = liveToken;
        const res = await fetch(
          buildUrl({ mode, speed, combo, structure, handler, repeats, timeoutMin, seed }),
          { signal: ac.signal, headers },
        );
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        // Parse SSE frames delimited by a blank line.
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let sep: number;
          while ((sep = buf.indexOf("\n\n")) !== -1) {
            const frame = buf.slice(0, sep);
            buf = buf.slice(sep + 2);
            const line = frame.split("\n").find((l) => l.startsWith("data:"));
            if (!line) continue;
            const payload = line.slice(5).trim();
            if (payload === "[DONE]") {
              setPhase((p) => (p === "running" ? "done" : p));
              continue;
            }
            try {
              const obj = JSON.parse(payload);
              if (obj.error) {
                setError(String(obj.error));
                continue;
              }
              const r = obj as Run;
              setRun(r);
              if (r.result) setPhase(r.result.status === "completed" ? "done" : "failed");
            } catch {
              // ignore malformed frame
            }
          }
        }
        setPhase((p) => (p === "running" ? "done" : p));
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          setError((e as Error).message);
          setPhase("failed");
        }
      }
    })();

    return () => ac.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, speed, combo, structure, handler, repeats, timeoutMin, seed, liveToken, enabled, epoch]);

  return { run, phase, error };
}

import { useEffect, useState } from "react";

// Re-renders once per second while `active` is true. Drives the "yes, it's
// still alive" elapsed timers on the running stage, its current tool, and the
// page subtitle - the handoff calls these out as the primary liveness cue.
export function useTicker(active: boolean): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [active]);
  return tick;
}

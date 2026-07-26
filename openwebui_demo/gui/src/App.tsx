import { useEffect, useMemo, useState } from "react";

import { Footer, TopBar, type ViewId } from "./components/Shell";
import { SetupView } from "./views/SetupView";
import { TopologyView } from "./views/TopologyView";
import { LiveView } from "./views/LiveView";
import { ResultsView } from "./views/ResultsView";
import { ReplayView } from "./views/ReplayView";

import { MOCK_RUN, MOCK_RUN_DONE } from "./data/mockRun";
import { isValidCombo } from "./data/reference";
import type { RunPhase } from "./data/types";
import { useRunStream } from "./sse/useRunStream";

export type Density = "compact" | "regular" | "comfy";
export type IterStyle = "stacked" | "tabs";

export interface Prefs {
  density: Density;
  iterStyle: IterStyle;
  showTokenBudget: boolean;
}

export interface SetupState {
  structure: string;
  handler: string;
  missionPreset: string;
  model: string;
  repeats: number;
  timeoutMin: number;
  seed: number;
  initialParams: { AREA: number; ASPECT_RATIO: number; SCALE_FACTOR: number };
}

const DEFAULT_SETUP: SetupState = {
  structure: "sequential",
  handler: "iterative_feedback",
  missionPreset: "f25",
  model: "anthropic/claude-sonnet-4-20250514",
  repeats: 1,
  timeoutMin: 60,
  seed: 42,
  initialParams: { AREA: 130.1, ASPECT_RATIO: 11.0, SCALE_FACTOR: 1.3 },
};

export function App() {
  const [view, setView] = useState<ViewId>("setup");
  const [hasLaunched, setHasLaunched] = useState(false);
  const [setup, setSetup] = useState<SetupState>(DEFAULT_SETUP);
  const [topologyPattern, setTopologyPattern] = useState<string>("sequential");
  const [prefs] = useState<Prefs>({
    density: "regular",
    iterStyle: "stacked",
    showTokenBudget: true,
  });

  // Live view: mock fixture by default (faithful, free). Opting into a real
  // live run streams from chat_server (mode=live) - that spawns the paid 5-MCP
  // pipeline, so it's gated behind an explicit user toggle.
  const [phase, setPhase] = useState<RunPhase>("idle");
  const [connectLive, setConnectLive] = useState(false);
  const [liveToken, setLiveToken] = useState<string>("");
  const liveStream = useRunStream({
    mode: "live",
    enabled: connectLive,
    combo: `mdo_f25_${setup.structure}_${setup.handler}`,
    structure: setup.structure,
    handler: setup.handler,
    repeats: setup.repeats,
    timeoutMin: setup.timeoutMin,
    seed: setup.seed,
    liveToken,
  });

  // Live runs spend real money and are token-gated on the server. Prompt for
  // the token at runtime (it is never baked into the static build).
  function handleConnectLive(on: boolean) {
    if (!on) {
      setConnectLive(false);
      return;
    }
    const t = window.prompt(
      "Live run token (MAS_LIVE_TOKEN on the server).\n" +
        "This spawns the real, paid 5-MCP pipeline.",
    );
    if (t) {
      setLiveToken(t);
      setConnectLive(true);
    }
  }

  const liveRun = connectLive && liveStream.run ? liveStream.run : MOCK_RUN;
  const livePhase = connectLive ? liveStream.phase : phase;
  const doneRun = MOCK_RUN_DONE;

  const runId = liveRun.meta.run_id;

  function launch() {
    setHasLaunched(true);
    setPhase("running");
    setTopologyPattern(setup.structure);
    setView("topology");
  }

  // Keep the topology preview aligned to the selected structure until launch.
  useEffect(() => {
    if (!hasLaunched) setTopologyPattern(setup.structure);
  }, [setup.structure, hasLaunched]);

  const footerMeta = useMemo(() => liveRun.meta, [liveRun]);

  return (
    <div className={`app density-${prefs.density}`}>
      <TopBar
        view={view}
        onView={setView}
        hasLaunched={hasLaunched}
        runId={runId}
        phase={livePhase}
      />

      <main>
        {view === "setup" && (
          <SetupView setup={setup} onChange={setSetup} onLaunch={launch} />
        )}
        {view === "topology" && (
          <TopologyView
            selectedStructure={setup.structure}
            pattern={topologyPattern}
            onPattern={setTopologyPattern}
            canLaunch={isValidCombo(setup.structure, setup.handler)}
            onLaunch={() => {
              if (!hasLaunched) launch();
              setView("live");
            }}
          />
        )}
        {view === "live" && (
          <LiveView
            run={liveRun}
            prefs={prefs}
            phase={livePhase}
            onPhase={setPhase}
            connectLive={connectLive}
            onConnectLive={handleConnectLive}
            liveError={liveStream.error}
          />
        )}
        {view === "results" && <ResultsView run={doneRun} prefs={prefs} />}
        {view === "replay" && <ReplayView run={liveRun} prefs={prefs} />}
      </main>

      <Footer meta={footerMeta} />
    </div>
  );
}

export default App;

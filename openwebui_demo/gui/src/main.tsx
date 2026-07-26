import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./styles/fonts.css";
import "./styles/tokens.css";
import "./styles/app.css";
import "./styles/topology.css";

import { App } from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

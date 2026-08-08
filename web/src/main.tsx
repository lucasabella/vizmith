import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import Boundary from "./Boundary";
import "./styles/tokens.css";
import "./styles/shell.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* The outer one. A render that throws for a reason nobody predicted should cost what
        the tab holds rather than the tab, and should say what happened, since the person
        who hit it is the one who can report it. The canvas has one of its own inside. */}
    <Boundary what="interface">
      <App />
    </Boundary>
  </StrictMode>,
);

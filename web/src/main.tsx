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
        who hit it is the one who can report it. The canvas has one of its own inside, and
        that is the one that can offer a way back that keeps anything: React has already
        unmounted the application by the time this draws, so what it holds — the spec in
        the editor, the dashboard being arranged — is gone either way, and the note says
        so rather than letting the button imply otherwise. */}
    <Boundary
      what="interface"
      note="What was on screen is gone, so this starts the interface over rather than restoring it."
    >
      <App />
    </Boundary>
  </StrictMode>,
);

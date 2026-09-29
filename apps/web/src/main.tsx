import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// The one token set both client surfaces read (docs/design/assets/README.md). Imported from
// where it is maintained rather than copied, so the console cannot drift from it.
import "../../../docs/design/assets/tokens.css";
import "./app.css";
import { App } from "./App";

const root = document.getElementById("root");
if (!root) {
  throw new Error("index.html has no #root element to mount the console into");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import { ensureI18n, initMockBridge } from "@/mock";

// Always bootstrap i18n (window.t) — works in both mock and production AstrBot
ensureI18n();
// Auto-activate mock API bridge only when AstrBot backend is absent
initMockBridge();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

// Hide the bootstrap fallback UI once React has mounted successfully
window.hideBootstrap?.();

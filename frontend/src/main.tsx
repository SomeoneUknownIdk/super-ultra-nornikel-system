import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@xyflow/react/dist/style.css";
import "./styles/tokens.css";
import "./styles/global.css";
import "./styles/print.css";
import { AppProviders } from "./app/providers";
import { AppRouter } from "./app/router";
import { LoginGate } from "./features/auth/LoginGate";

createRoot(document.getElementById("root")!).render(
  <StrictMode><AppProviders><LoginGate><AppRouter /></LoginGate></AppProviders></StrictMode>,
);

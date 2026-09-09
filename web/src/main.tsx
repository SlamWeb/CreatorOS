import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./theme.css";
import "./styles.css";
import "./agent.css";
import "./components/creative-studio.css";

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "@fontsource-variable/manrope";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/ibm-plex-mono/600.css";
import "./theme.css";
import "./styles.css";
import "./agent.css";
import "./components/creative-studio.css";

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);

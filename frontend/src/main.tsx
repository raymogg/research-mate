import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

// No StrictMode: its double-mounted effects would open two reading sessions per visit.
createRoot(document.getElementById("root")!).render(<App />);

import React from "react";
import ReactDOM from "react-dom/client";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { OrbWindow } from "./components/OrbWindow";
import { MainApp } from "./components/MainApp";
import "./App.css";

const label = getCurrentWindow().label;

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    {label === "orb" ? <OrbWindow /> : <MainApp />}
  </React.StrictMode>,
);

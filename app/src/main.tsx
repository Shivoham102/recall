import ReactDOM from "react-dom/client";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { OrbWindow } from "./components/OrbWindow";
import { MainApp } from "./components/MainApp";
import { LoginScreen } from "./components/LoginScreen";
import { LoadingScreen } from "./components/LoadingScreen";
import { useAuth } from "./hooks/useAuth";
import "./App.css";

const label = getCurrentWindow().label;

function AppRoot() {
  const { user, isLoading, logout, refreshAuth } = useAuth();

  if (isLoading) return <LoadingScreen />;
  if (!user) return <LoginScreen onLogin={refreshAuth} />;
  return <MainApp user={user} onLogout={logout} />;
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  label === "orb" ? <OrbWindow /> : <AppRoot />,
);

import ReactDOM from "react-dom/client";
import { getCurrentWindow } from "@tauri-apps/api/window";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import { OrbWindow } from "./components/OrbWindow";
import { NotificationCard } from "./components/NotificationCard";
import { MainApp } from "./components/MainApp";
import { LoginScreen } from "./components/LoginScreen";
import { LoadingScreen } from "./components/LoadingScreen";
import { useAuth } from "./hooks/useAuth";
import { ThemeProvider, initTheme } from "./hooks/useTheme";
import "./App.css";

// Apply the persisted theme synchronously, before first paint, to avoid a
// flash of the wrong theme. Runs for both the main and orb windows.
initTheme();

const label = getCurrentWindow().label;

function AppRoot() {
  const { user, isLoading, logout, refreshAuth } = useAuth();

  return (
    <ThemeProvider>
      {isLoading ? (
        <LoadingScreen />
      ) : !user ? (
        <LoginScreen onLogin={refreshAuth} />
      ) : (
        <MainApp user={user} onLogout={logout} />
      )}
    </ThemeProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  label === "orb" ? <OrbWindow /> : label === "notif" ? <NotificationCard /> : <AppRoot />,
);

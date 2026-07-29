import { useEffect, useState } from "react";
import "./styles/globals.css";
import { Sidebar } from "./components/layout/Sidebar";
import { Topbar } from "./components/layout/Topbar";
import { LoginPage } from "./components/auth/LoginPage";
import { StudioRoot } from "./modes/studio/StudioRoot";
import { EvalRoot } from "./modes/eval/EvalRoot";
import { OperatorRoot } from "./modes/operator/OperatorRoot";
import { RunCostContext } from "./RunCostContext";
import { currentUsername, isAuthed, rehydrate } from "./api/client";

type Mode = "studio" | "eval" | "operator";

export default function App() {
  const [username, setUsername] = useState(currentUsername());
  const [mode, setMode] = useState<Mode>("studio");
  const [runCost, setRunCost] = useState(0);
  const [loggedIn, setLoggedIn] = useState(isAuthed());

  // On a fresh page load the in-memory identity is empty, but the HttpOnly
  // auth cookie may still be valid. Recover the session from it before
  // deciding whether to show the login screen; `checking` prevents a flash
  // of the login page while /auth/me is in flight.
  const [checking, setChecking] = useState(!isAuthed());

  useEffect(() => {
    if (isAuthed()) return; // live session already; nothing to recover
    let cancelled = false;
    rehydrate().then((user) => {
      if (cancelled) return;
      if (user) {
        setLoggedIn(true);
        setUsername(user.username);
      }
      setChecking(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (checking) {
    return null; // or a splash/spinner; avoids flashing the login page
  }

  if (!loggedIn) {
    return <LoginPage onLogin={(u) => { setLoggedIn(true); setUsername(u); }} />;
  }

  return (
    <div style={{ display: "flex" }}>
      <Sidebar mode={mode} onModeChange={setMode} username={username ?? ""} />
      <div style={{ marginLeft: "var(--sidebar-width)", flex: 1, minHeight: "100vh", display: "flex", flexDirection: "column" }}>
        <Topbar mode={mode} runCostUsd={runCost} />
        <RunCostContext.Provider value={setRunCost}>
          <main style={{ flex: 1, padding: 24 }}>
            {mode === "studio" && <StudioRoot />}
            {mode === "eval" && <EvalRoot />}
            {mode === "operator" && <OperatorRoot />}
          </main>
        </RunCostContext.Provider>
      </div>
    </div>
  );
}
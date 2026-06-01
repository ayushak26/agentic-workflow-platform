import { useState } from "react";
import "./styles/globals.css";
import { Sidebar } from "./components/layout/Sidebar";
import { Topbar } from "./components/layout/Topbar";
import { LoginPage } from "./components/auth/LoginPage";
import { StudioRoot } from "./modes/studio/StudioRoot";
import { EvalRoot } from "./modes/eval/EvalRoot";
import { OperatorRoot } from "./modes/operator/OperatorRoot";
import { RunCostContext } from "./RunCostContext";

type Mode = "studio" | "eval" | "operator";

export default function App() {
  const [username, setUsername] = useState("");
  const [mode, setMode] = useState<Mode>("studio");
  const [runCost, setRunCost] = useState(0);
  const [loggedIn, setLoggedIn] = useState(false);

  if (!loggedIn) {
    return <LoginPage onLogin={(u) => { setLoggedIn(true); setUsername(u); }} />;
  }

  return (
    <div style={{ display: "flex" }}>
      <Sidebar mode={mode} onModeChange={setMode} username={username} />
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
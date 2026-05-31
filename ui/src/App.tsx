import { useState } from "react";
import "./styles/globals.css";
import { Sidebar } from "./components/layout/Sidebar";
import { Topbar }  from "./components/layout/Topbar";
import { LoginPage } from "./components/auth/LoginPage";

type Mode = "studio" | "eval" | "operator";

export default function App() {
  const [token, setToken]       = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [mode, setMode]         = useState<Mode>("studio");
  const [runCost, setRunCost]   = useState(0);

  if (!token) {
    return (
      <LoginPage onLogin={(t, u) => { setToken(t); setUsername(u); }} />
    );
  }

  return (
    <div style={{ display: "flex" }}>
      <Sidebar mode={mode} onModeChange={setMode} username={username} />
      <div style={{ marginLeft: "var(--sidebar-width)", flex: 1, minHeight: "100vh", display: "flex", flexDirection: "column" }}>
        <Topbar mode={mode} runCostUsd={runCost} />
        <main style={{ flex: 1, padding: 24 }}>
          {/* Phase 9 Studio / Eval / Operator views mount here */}
          <p style={{ color: "var(--eur-text-secondary)" }}>
            {mode} mode — Eurskem AI platform
          </p>
        </main>
      </div>
    </div>
  );
}
import { useEffect, useState } from "react";
import "./styles/globals.css";
import { Sidebar } from "./components/layout/Sidebar";
import { Topbar } from "./components/layout/Topbar";
import { LoginPage } from "./components/auth/LoginPage";
import { BrandMark } from "./components/ui/BrandMark";
import { StudioRoot } from "./modes/studio/StudioRoot";
import { EvalRoot } from "./modes/eval/EvalRoot";
import { OperatorRoot } from "./modes/operator/OperatorRoot";
import { KnowledgeRoot } from "./modes/knowledge/KnowledgeRoot";
import { RunCostContext } from "./RunCostContext";
import { currentUsername, isAuthed, rehydrate } from "./api/client";
import type { RunCostSummary } from "./api/types";

type Mode = "studio" | "eval" | "operator" | "knowledge";

export default function App() {
  const [username, setUsername] = useState(currentUsername());
  const [mode, setMode] = useState<Mode>("studio");
  const [runCostSummary, setRunCostSummary] = useState<RunCostSummary | null>(null);
  const [loggedIn, setLoggedIn] = useState(isAuthed());
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => (
    window.localStorage.getItem('eurskem.sidebar.collapsed') === 'true'
  ));
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

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

  useEffect(() => {
    window.localStorage.setItem('eurskem.sidebar.collapsed', String(sidebarCollapsed));
  }, [sidebarCollapsed]);

  if (checking) {
    return (
      <div className="brand-splash" role="status" aria-label="Loading Eurskem AI">
        <div className="brand-splash-card">
          <BrandMark size="md" />
          <span className="mt-5 h-5 w-5 animate-spin rounded-full border-2 border-accent-400 border-t-transparent" />
          <span className="mt-3 text-xs text-ink-300">Preparing your workspaceâ¦</span>
        </div>
      </div>
    );
  }

  if (!loggedIn) {
    return <LoginPage onLogin={(u) => { setLoggedIn(true); setUsername(u); }} />;
  }

  return (
    <div className="app-shell">
      <Sidebar
        collapsed={sidebarCollapsed}
        mobileOpen={mobileNavOpen}
        mode={mode}
        onCloseMobile={() => setMobileNavOpen(false)}
        onCollapsedChange={setSidebarCollapsed}
        onModeChange={(nextMode) => {
          setMode(nextMode);
          setMobileNavOpen(false);
        }}
        username={username ?? ""}
      />
      <div className={`app-main ${sidebarCollapsed ? 'app-main--collapsed' : ''}`}>
        <Topbar mode={mode} onOpenNavigation={() => setMobileNavOpen(true)} runCostUsd={runCostSummary?.total_usd ?? 0} />
        <RunCostContext.Provider value={setRunCostSummary}>
          <main className="app-content">
            {mode === "studio" && <StudioRoot />}
            {mode === "knowledge" && <KnowledgeRoot />}
            {mode === "eval" && <EvalRoot />}
            {mode === "operator" && <OperatorRoot />}
          </main>
        </RunCostContext.Provider>
      </div>
    </div>
  );
}
